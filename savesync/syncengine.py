"""동기화 엔진.

규칙(matcher)에 맞는 파일들을 로컬↔드라이브 양방향으로 동기화한다.

동기화 규칙
-----------
- 양쪽 모두 존재 + mtime 차이가 tolerance 이내  → 변경 없음(skip)
- 양쪽 모두 존재 + mtime 다름                   → 충돌. conflict_policy 로 방향 결정
      * newer : 더 최신 쪽이 이김
      * local : 항상 로컬 → 드라이브
      * drive : 항상 드라이브 → 로컬
      * ask   : on_conflict 콜백으로 사용자에게 질문 (없으면 newer 로 폴백)
- 로컬에만 존재  → 드라이브로 업로드(신규)
- 드라이브에만 존재 → 로컬로 다운로드(신규)
- 삭제는 전파하지 않는다(세이브 데이터 보호). 한쪽에서 지운 파일은 다른 쪽에서 복원됨.

덮어쓰기가 일어나기 직전에는 항상 대상(곧 사라질 버전)을 백업한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from . import matcher
from .backup import BackupSession
from .config import (
    Config, Profile, CONFLICT_NEWER, CONFLICT_LOCAL, CONFLICT_DRIVE, CONFLICT_ASK,
)

if TYPE_CHECKING:  # 런타임에 google 라이브러리를 요구하지 않도록 타입 전용 임포트
    from .drive import DriveClient

# 충돌 시 사용자에게 질문하는 콜백.
# (rel_path, local_mtime_epoch, drive_mtime_epoch) -> "local" | "drive" | "skip"
ConflictResolver = Callable[[str, float, float], str]


@dataclass
class SyncStats:
    name: str = ""               # 프로필 이름 (알림/로그 표시용)
    uploaded_new: int = 0
    downloaded_new: int = 0
    uploaded_overwrite: int = 0
    downloaded_overwrite: int = 0
    skipped: int = 0
    backed_up: int = 0
    conflicts_resolved: int = 0
    errors: list[str] = field(default_factory=list)

    def has_activity(self) -> bool:
        """실제로 업로드/다운로드/덮어쓰기 등 변경이 있었는지(변경 없음=False)."""
        return any((self.uploaded_new, self.downloaded_new,
                    self.uploaded_overwrite, self.downloaded_overwrite,
                    self.backed_up))

    def summary(self) -> str:
        return (
            f"신규 업로드 {self.uploaded_new}, 신규 다운로드 {self.downloaded_new}, "
            f"덮어쓰기(↑{self.uploaded_overwrite}/↓{self.downloaded_overwrite}), "
            f"백업 {self.backed_up}, 변경없음 {self.skipped}"
            + (f", 오류 {len(self.errors)}" if self.errors else "")
        )


def _set_local_mtime(path: Path, epoch: float) -> None:
    os.utime(path, (epoch, epoch))


def sync_profile(
    drive: DriveClient,
    cfg: Config,
    profile: Profile,
    log: Callable[[str], None] = lambda m: None,
    on_conflict: ConflictResolver | None = None,
) -> SyncStats:
    stats = SyncStats(name=profile.name)

    # 미지정 방어: 빈 경로는 Path('.') 로 해석되어 .is_dir() 이 True 가 되므로
    # (실행 파일 위치가 동기화되는 사고) 명시적으로 막는다.
    if not profile.has_local_folder():
        stats.errors.append("로컬 세이브 폴더가 지정되지 않았습니다.")
        return stats

    local_root = Path(profile.local_folder)
    if not local_root.is_dir():
        stats.errors.append(f"로컬 폴더 없음: {profile.local_folder}")
        return stats

    # 드라이브 폴더 ID 확보: 미리 지정돼 있으면(테스트/캐시) 그대로 쓰고,
    # 없으면 'SaveSync/<폴더이름>' 을 찾거나 만들어 그 ID 를 쓴다.
    drive_folder_id = profile.drive_folder_id
    if not drive_folder_id:
        try:
            drive_folder_id = drive.ensure_profile_folder(
                profile.drive_folder_name or profile.name
            )
        except Exception as e:
            stats.errors.append(f"드라이브 폴더 준비 실패: {e}")
            return stats

    tol = cfg.mtime_tolerance_seconds
    backup = BackupSession(cfg.backup_dir, profile.name, cfg.backup_enabled)

    # 1) 양쪽 파일 목록 수집
    local_files = {
        rel: (local_root / rel)
        for rel in matcher.iter_local_files(profile.local_folder, profile.rules)
    }
    drive_all = drive.list_files_recursive(drive_folder_id)
    # 드라이브 쪽도 규칙으로 필터 (드라이브에 무관한 파일이 섞여 있을 수 있음)
    drive_files = {
        rel: meta for rel, meta in drive_all.items()
        if matcher.matches(rel, profile.rules)
    }

    all_rels = sorted(set(local_files) | set(drive_files))
    log(f"[{profile.name}] 로컬 {len(local_files)}개 / 드라이브 {len(drive_files)}개 대상")

    for rel in all_rels:
        try:
            in_local = rel in local_files
            in_drive = rel in drive_files

            if in_local and not in_drive:
                drive.upload_new(drive_folder_id, rel, local_files[rel])
                stats.uploaded_new += 1
                log(f"  ↑ 신규 업로드: {rel}")

            elif in_drive and not in_local:
                _download(drive, drive_files[rel], local_root / rel)
                stats.downloaded_new += 1
                log(f"  ↓ 신규 다운로드: {rel}")

            else:
                local_mtime = local_files[rel].stat().st_mtime
                drive_mtime = drive_files[rel]["modified_epoch"]
                if abs(local_mtime - drive_mtime) <= tol:
                    stats.skipped += 1
                    continue

                direction = _decide(cfg.conflict_policy, local_mtime, drive_mtime,
                                    rel, on_conflict)
                stats.conflicts_resolved += 1

                if direction == "skip":
                    stats.skipped += 1
                    log(f"  = 건너뜀(사용자 선택): {rel}")
                elif direction == "local":
                    # 드라이브 버전을 백업 후, 로컬로 덮어쓰기
                    data = drive.download_bytes(drive_files[rel]["id"])
                    if backup.backup_drive_bytes(data, rel):
                        stats.backed_up += 1
                    drive.update_existing(drive_files[rel]["id"], local_files[rel])
                    stats.uploaded_overwrite += 1
                    log(f"  ↑ 덮어쓰기(로컬→드라이브): {rel}")
                else:  # "drive"
                    # 로컬 버전을 백업 후, 드라이브 내용으로 로컬 덮어쓰기
                    if backup.backup_local(local_files[rel], rel):
                        stats.backed_up += 1
                    _download(drive, drive_files[rel], local_root / rel)
                    stats.downloaded_overwrite += 1
                    log(f"  ↓ 덮어쓰기(드라이브→로컬): {rel}")
        except Exception as e:  # 한 파일 실패가 전체를 막지 않도록
            msg = f"{rel}: {e}"
            stats.errors.append(msg)
            log(f"  ! 오류: {msg}")

    log(f"[{profile.name}] 완료 — {stats.summary()}")
    return stats


def _decide(policy: str, local_mtime: float, drive_mtime: float,
            rel: str, on_conflict: ConflictResolver | None) -> str:
    if policy == CONFLICT_LOCAL:
        return "local"
    if policy == CONFLICT_DRIVE:
        return "drive"
    if policy == CONFLICT_ASK:
        if on_conflict is not None:
            return on_conflict(rel, local_mtime, drive_mtime)
        # 백그라운드 등 질문 불가 상황 → newer 로 폴백
        return "local" if local_mtime >= drive_mtime else "drive"
    # CONFLICT_NEWER (기본)
    return "local" if local_mtime >= drive_mtime else "drive"


def _download(drive: DriveClient, meta: dict, dest: Path) -> None:
    data = drive.download_bytes(meta["id"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    # 로컬 mtime 을 드라이브 기준으로 맞춰 재동기화 루프 방지
    _set_local_mtime(dest, meta["modified_epoch"])


def sync_all(
    drive: DriveClient,
    cfg: Config,
    log: Callable[[str], None] = lambda m: None,
    on_conflict: ConflictResolver | None = None,
) -> list[SyncStats]:
    results = []
    for profile in cfg.profiles:
        if not profile.enabled:
            continue
        # 로컬 세이브 폴더가 지정되지 않은 프로필은 건너뛴다.
        # (드라이브에서 가져온 직후 등) 미지정 상태로 동기화하면 의도치 않은
        # 폴더(예: 실행 파일 위치)가 동기화 대상이 되는 사고를 막기 위함.
        if not profile.has_local_folder():
            log(f"[{profile.name}] 로컬 세이브 폴더가 지정되지 않아 건너뜁니다.")
            continue
        results.append(sync_profile(drive, cfg, profile, log, on_conflict))
    return results
