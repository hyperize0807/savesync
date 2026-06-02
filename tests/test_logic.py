"""핵심 로직 테스트 (google 라이브러리 불필요).

가짜 in-memory Drive 클라이언트로 sync_profile 전체 흐름을 검증한다.
실행: python -m tests.test_logic   (savesync 패키지 루트에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# 콘솔 인코딩 보정(Windows cp949에서도 한글/기호 출력)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 패키지 임포트 경로 보정
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from savesync import matcher
from savesync.config import Config, Profile, Rules
from savesync.config import CONFLICT_NEWER, CONFLICT_LOCAL, CONFLICT_DRIVE
from savesync.syncengine import sync_profile

_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok: {msg}")
    else:
        print(f"  FAIL: {msg}")
        _failures.append(msg)


class FakeDrive:
    """메모리 상의 가짜 드라이브. relpath -> {bytes, modified_epoch}."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self._next_id = 1

    def list_files_recursive(self, root_id):
        return {
            rel: {"id": rel, "modified_epoch": m["modified_epoch"], "size": len(m["bytes"])}
            for rel, m in self.files.items()
        }

    def upload_new(self, root_id, rel_path, local_file: Path):
        self.files[rel_path] = {
            "bytes": local_file.read_bytes(),
            "modified_epoch": local_file.stat().st_mtime,
        }
        return rel_path

    def update_existing(self, file_id, local_file: Path):
        self.files[file_id] = {
            "bytes": local_file.read_bytes(),
            "modified_epoch": local_file.stat().st_mtime,
        }

    def download_bytes(self, file_id):
        return self.files[file_id]["bytes"]


def write(p: Path, content: str, mtime: float | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def base_cfg(policy=CONFLICT_NEWER, backup_dir=""):
    return Config(conflict_policy=policy, backup_enabled=True,
                  backup_dir=backup_dir, mtime_tolerance_seconds=2)


def make_profile(local, rules=None):
    return Profile(name="t", local_folder=str(local), drive_folder_id="ROOT",
                   rules=rules or Rules(include_extensions=[".sav"]))


def test_matcher():
    print("test_matcher")
    r = Rules(include_extensions=[".sav"], exclude_globs=["*.tmp"])
    check(matcher.matches("game.sav", r), ".sav 포함")
    check(not matcher.matches("game.exe", r), ".exe 제외")
    check(not matcher.matches("game.tmp", r), "*.tmp 제외 우선")
    r2 = Rules(include_globs=["save*.*"])
    check(matcher.matches("save01.dat", r2), "glob 포함")
    check(not matcher.matches("config.dat", r2), "glob 미일치")
    r3 = Rules()  # 규칙 없음 → 모두 포함(제외만 적용)
    check(matcher.matches("anything.bin", r3), "규칙없음=모두포함")


def test_new_files_both_ways():
    print("test_new_files_both_ways")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"
        backup = Path(d) / "backup"
        local.mkdir()
        write(local / "a.sav", "AAA")          # 로컬에만
        drive = FakeDrive()
        drive.files["b.sav"] = {"bytes": b"BBB", "modified_epoch": time.time()}  # 드라이브에만

        stats = sync_profile(drive, base_cfg(backup_dir=str(backup)), make_profile(local))
        check(drive.files.get("a.sav", {}).get("bytes") == b"AAA", "a.sav 업로드됨")
        check((local / "b.sav").exists(), "b.sav 다운로드됨")
        check(stats.uploaded_new == 1 and stats.downloaded_new == 1, "신규 카운트 정확")
        check(stats.backed_up == 0, "신규는 백업 불필요")


def test_conflict_newer_local_wins():
    print("test_conflict_newer (로컬이 최신)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; backup = Path(d) / "backup"; local.mkdir()
        now = time.time()
        write(local / "s.sav", "NEW_LOCAL", mtime=now)
        drive = FakeDrive()
        drive.files["s.sav"] = {"bytes": b"OLD_DRIVE", "modified_epoch": now - 100}

        stats = sync_profile(drive, base_cfg(CONFLICT_NEWER, str(backup)), make_profile(local))
        check(drive.files["s.sav"]["bytes"] == b"NEW_LOCAL", "드라이브가 로컬 최신본으로 갱신")
        check(stats.uploaded_overwrite == 1, "덮어쓰기(업로드) 카운트")
        check(stats.backed_up == 1, "덮어쓰기 전 드라이브 원본 백업됨")
        # 백업에 드라이브 원본이 있어야 함
        backups = list(backup.rglob("s.sav"))
        check(any(b.read_bytes() == b"OLD_DRIVE" for b in backups), "백업에 이전 드라이브본 보존")


def test_conflict_newer_drive_wins():
    print("test_conflict_newer (드라이브가 최신)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; backup = Path(d) / "backup"; local.mkdir()
        now = time.time()
        write(local / "s.sav", "OLD_LOCAL", mtime=now - 100)
        drive = FakeDrive()
        drive.files["s.sav"] = {"bytes": b"NEW_DRIVE", "modified_epoch": now}

        sync_profile(drive, base_cfg(CONFLICT_NEWER, str(backup)), make_profile(local))
        check((local / "s.sav").read_text() == "NEW_DRIVE", "로컬이 드라이브 최신본으로 갱신")
        backups = list(backup.rglob("s.sav"))
        check(any(b.read_text() == "OLD_LOCAL" for b in backups), "백업에 이전 로컬본 보존")
        # mtime 동기화로 재실행 시 변경 없어야 함
        st2 = sync_profile(drive, base_cfg(CONFLICT_NEWER, str(backup)), make_profile(local))
        check(st2.skipped == 1 and st2.conflicts_resolved == 0, "재실행 시 루프 없음(skip)")


def test_policy_force_local():
    print("test_policy_force_local (드라이브가 더 최신이어도 로컬 우선)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; backup = Path(d) / "backup"; local.mkdir()
        now = time.time()
        write(local / "s.sav", "LOCAL", mtime=now - 100)  # 로컬이 오래됨
        drive = FakeDrive()
        drive.files["s.sav"] = {"bytes": b"DRIVE", "modified_epoch": now}

        sync_profile(drive, base_cfg(CONFLICT_LOCAL, str(backup)), make_profile(local))
        check(drive.files["s.sav"]["bytes"] == b"LOCAL", "정책=local: 드라이브가 로컬본으로 덮어써짐")


def test_subfolders():
    print("test_subfolders (하위 폴더 미러링)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; backup = Path(d) / "backup"; local.mkdir()
        write(local / "slot1" / "a.sav", "A")
        drive = FakeDrive()
        sync_profile(drive, base_cfg(backup_dir=str(backup)),
                     make_profile(local, Rules(include_extensions=[".sav"], recursive=True)))
        check("slot1/a.sav" in drive.files, "하위 폴더 경로로 업로드됨")


def main():
    for t in [test_matcher, test_new_files_both_ways, test_conflict_newer_local_wins,
              test_conflict_newer_drive_wins, test_policy_force_local, test_subfolders]:
        t()
    print()
    if _failures:
        print(f"실패 {len(_failures)}건:")
        for f in _failures:
            print(" -", f)
        sys.exit(1)
    print("모든 테스트 통과 ✓")


if __name__ == "__main__":
    main()
