"""핵심 로직 테스트 (google 라이브러리 불필요).

가짜 in-memory Drive 클라이언트로 sync_profile 전체 흐름을 검증한다.
실행: python -m tests.test_logic   (savesync 패키지 루트에서)
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
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
from savesync.config import (
    export_profiles_payload, merge_imported_profiles, PROFILES_BLOB_NAME,
)
from savesync.syncengine import sync_profile, sync_all
from savesync import updater

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
        self._lock = threading.Lock()  # 병렬 동기화 테스트용 스레드 안전 보장

    def list_files_recursive(self, root_id):
        with self._lock:
            return {
                rel: {"id": rel, "modified_epoch": m["modified_epoch"],
                      "size": len(m["bytes"])}
                for rel, m in self.files.items()
            }

    def upload_new(self, root_id, rel_path, local_file: Path):
        entry = {
            "bytes": local_file.read_bytes(),
            "modified_epoch": local_file.stat().st_mtime,
        }
        with self._lock:
            self.files[rel_path] = entry
        return rel_path

    def update_existing(self, file_id, local_file: Path):
        entry = {
            "bytes": local_file.read_bytes(),
            "modified_epoch": local_file.stat().st_mtime,
        }
        with self._lock:
            self.files[file_id] = entry

    def download_bytes(self, file_id):
        with self._lock:
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


def test_matcher_ext_and_glob():
    print("test_matcher_ext_and_glob (확장자+패턴은 AND)")
    # 확장자 '.sav' + 패턴 'G3P_II*' → 둘 다 만족해야 포함
    r = Rules(include_extensions=[".sav"], include_globs=["G3P_II*"])
    check(matcher.matches("G3P_II_01.sav", r), "확장자✓+패턴✓ → 포함")
    check(not matcher.matches("G30001.sav", r), "확장자✓+패턴✗ → 제외(버그 회귀)")
    check(not matcher.matches("G3P_II_save.dat", r), "확장자✗+패턴✓ → 제외")
    check(not matcher.matches("note.txt", r), "둘 다 불일치 → 제외")
    # 한쪽만 지정하면 그 조건만 적용
    r_ext = Rules(include_extensions=[".sav"])
    check(matcher.matches("G30001.sav", r_ext), "확장자만 지정 → .sav 포함")
    r_glob = Rules(include_globs=["G3P_II*"])
    check(matcher.matches("G3P_II_x.dat", r_glob), "패턴만 지정 → 패턴 일치 포함")
    check(not matcher.matches("G30001.sav", r_glob), "패턴만 지정 → 패턴 불일치 제외")
    # 여러 확장자 중 하나 + 패턴
    r_multi = Rules(include_extensions=[".sav", ".dat"], include_globs=["G3P_II*"])
    check(matcher.matches("G3P_II_a.dat", r_multi), "확장자 OR(.dat)✓+패턴✓ → 포함")
    check(not matcher.matches("G3P_II_a.bin", r_multi), "확장자 목록 불일치 → 제외")
    # 제외 패턴은 여전히 최우선
    r_excl = Rules(include_extensions=[".sav"], include_globs=["G3P_II*"],
                   exclude_globs=["*backup*"])
    check(not matcher.matches("G3P_II_backup.sav", r_excl), "제외 패턴 최우선")


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


def test_unset_local_folder_guard():
    print("test_unset_local_folder_guard (미지정 폴더 방어)")
    check(not Profile(name="x", local_folder="").has_local_folder(),
          "빈 문자열 = 미지정")
    check(not Profile(name="x", local_folder="   ").has_local_folder(),
          "공백만 = 미지정")
    check(Profile(name="x", local_folder=r"C:\Saves").has_local_folder(),
          "경로 있으면 지정됨")

    drive = FakeDrive()
    # sync_profile: 빈 폴더면 동기화하지 않고 오류로 기록(Path('') 사고 방지)
    st = sync_profile(drive, base_cfg(),
                      Profile(name="미지정", local_folder="", drive_folder_id="ROOT"))
    check(st.errors and not st.has_activity(), "빈 폴더는 동기화 안 하고 오류 기록")
    check(drive.files == {}, "드라이브에 아무것도 안 올라감(실행파일 폴더 사고 방지)")


def test_sync_all_skips_unset_profiles():
    print("test_sync_all_skips_unset_profiles (자동 동기화 건너뛰기)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; local.mkdir()
        write(local / "a.sav", "AAA")
        cfg = base_cfg()
        cfg.profiles = [
            Profile(name="지정됨", local_folder=str(local), drive_folder_id="ROOT",
                    rules=Rules(include_extensions=[".sav"])),
            Profile(name="미지정", local_folder="", drive_folder_id="ROOT",
                    rules=Rules(include_extensions=[".sav"])),
            Profile(name="비활성", local_folder="", enabled=False),
        ]
        logs: list[str] = []
        results = sync_all(drive := FakeDrive(), cfg, log=logs.append)
        names = [r.name for r in results]
        check(names == ["지정됨"], "지정된 프로필만 동기화됨")
        check(any("미지정" in m and "건너" in m for m in logs),
              "미지정 프로필 건너뜀이 로그에 기록됨")
        check(drive.files.get("a.sav", {}).get("bytes") == b"AAA", "지정 프로필은 정상 업로드")


def test_export_omits_local_folder():
    print("test_export_omits_local_folder")
    cfg = Config(profiles=[
        Profile(name="게임A", local_folder=r"C:\Saves\A",
                drive_folder_name="A폴더", drive_folder_id="ID_A",
                rules=Rules(include_extensions=[".sav"]), enabled=True),
    ])
    payload = export_profiles_payload(cfg)
    p0 = payload["profiles"][0]
    check(payload["version"] == 1, "version=1")
    check("local_folder" not in p0, "local_folder 제외됨")
    check(p0["name"] == "게임A", "name 보존")
    check(p0["drive_folder_name"] == "A폴더", "drive_folder_name 보존")
    check(p0["drive_folder_id"] == "ID_A", "drive_folder_id 포함")
    check(p0["rules"]["include_extensions"] == [".sav"], "rules 보존")
    assert "local_folder" not in p0


def test_merge_by_name_preserves_local():
    print("test_merge_by_name_preserves_local")
    cfg = Config(profiles=[
        Profile(name="게임A", local_folder=r"C:\old\A",
                drive_folder_name="OLD", drive_folder_id="X",
                rules=Rules(include_extensions=[".sav"]), enabled=True),
    ])
    payload = {"version": 1, "profiles": [{
        "name": "게임A", "drive_folder_name": "NEW", "drive_folder_id": "Y",
        "enabled": False, "rules": {"include_extensions": [".dat"]},
    }]}
    added, updated = merge_imported_profiles(cfg, payload)
    p = cfg.profiles[0]
    check((added, updated) == (0, 1), "동명=갱신(added0/updated1)")
    check(p.local_folder == r"C:\old\A", "local_folder 보존")
    check(p.drive_folder_name == "NEW", "drive_folder_name 갱신")
    check(p.drive_folder_id == "Y", "drive_folder_id 갱신")
    check(p.rules.include_extensions == [".dat"], "rules 갱신")
    check(p.enabled is False, "enabled 갱신")
    assert p.local_folder == r"C:\old\A"


def test_merge_adds_new_with_empty_local():
    print("test_merge_adds_new_with_empty_local")
    cfg = Config(profiles=[])
    payload = {"version": 1, "profiles": [{
        "name": "신규", "drive_folder_name": "신규폴더", "drive_folder_id": "Z",
        "enabled": True, "rules": {"include_extensions": [".sav"]},
    }]}
    added, updated = merge_imported_profiles(cfg, payload)
    check((added, updated) == (1, 0), "신규=추가(added1/updated0)")
    check(cfg.profiles[0].local_folder == "", "신규는 local_folder 빈 값")
    assert cfg.profiles[0].local_folder == ""


def test_export_import_round_trip():
    print("test_export_import_round_trip")
    src = Config(profiles=[
        Profile(name="게임A", local_folder=r"C:\Saves\A",
                drive_folder_name="A폴더", drive_folder_id="ID_A",
                rules=Rules(include_extensions=[".sav"])),
        Profile(name="게임B", local_folder=r"C:\Saves\B",
                drive_folder_name="B폴더", drive_folder_id="ID_B"),
    ])
    payload = export_profiles_payload(src)
    # 다른 기기: 빈 설정에 가져오기
    dst = Config(profiles=[])
    added, updated = merge_imported_profiles(dst, payload)
    check((added, updated) == (2, 0), "라운드트립 2개 추가")
    names = [p.name for p in dst.profiles]
    check(names == ["게임A", "게임B"], "이름 순서/내용 복원")
    check(all(p.local_folder == "" for p in dst.profiles), "로컬 폴더는 비어 있음")
    check(dst.profiles[0].drive_folder_id == "ID_A", "폴더 ID 복원")
    check(PROFILES_BLOB_NAME.endswith(".json"), "블롭 파일명 상수 확인")


def test_parse_version():
    print("test_parse_version")
    check(updater.parse_version("v1.2.3") == (1, 2, 3, 1), "v 접두사 제거")
    check(updater.parse_version("1.2.3") == (1, 2, 3, 1), "v 없이도 동작")
    check(updater.parse_version("v1.2") == (1, 2, 0, 1), "누락 자리 0 보정")
    check(updater.parse_version("1") == (1, 0, 0, 1), "한 자리도 보정")
    check(updater.parse_version("v1.2.3-rc1")[:3] == (1, 2, 3), "프리릴리스 코어 파싱")
    check(updater.parse_version("v1.2.3-rc1")[3] == 0, "프리릴리스 랭크 0")
    check(updater.parse_version("v1.2.3")[3] == 1, "정식 랭크 1")
    check(updater.parse_version("garbage") == (0, 0, 0, 1), "비숫자는 0")


def test_is_newer():
    print("test_is_newer")
    check(updater.is_newer("v1.0.2", "1.0.1") is True, "1.0.2 > 1.0.1")
    check(updater.is_newer("v1.0.1", "1.0.2") is False, "1.0.1 < 1.0.2")
    check(updater.is_newer("v1.0.2", "1.0.2") is False, "동일은 새 버전 아님")
    check(updater.is_newer("v1.0.2", "1.0.2-rc1") is True, "정식 > 같은 코어 프리릴리스")
    check(updater.is_newer("garbage", "1.0.2") is False, "잘못된 latest 는 False(안전)")


def test_pick_asset():
    print("test_pick_asset")
    assets = [{"name": "other.zip"}, {"name": "SaveSync.exe", "size": 100}]
    picked = updater.pick_asset(assets)
    check(picked is not None and picked["size"] == 100, "SaveSync.exe 선택")
    check(updater.pick_asset([{"name": "x"}]) is None, "없으면 None")
    check(updater.pick_asset([]) is None, "빈 목록은 None")


def test_render_update_script():
    print("test_render_update_script")
    s = updater.render_update_script()
    check('tasklist /FI "PID eq %PID%"' in s, "PID 종료 대기 루프 포함")
    check('move /y "%NEW%" "%OLD%"' in s, "move /y 교체 포함")
    check('start "" "%OLD%"' in s, "재실행 포함")
    check('del "%~f0"' in s, "자기 삭제 idiom 포함")
    check("%~1" in s and "%~2" in s and "%~3" in s, "argv(%~1..3) 참조")
    # 회귀 방지: timeout 은 stdin 리다이렉트 시 동작하지 않으므로 절대 쓰지 않는다.
    check("timeout " not in s, "timeout 미사용(회귀 방지)")
    check("ping -n" in s, "ping 기반 대기 사용")
    # move 재시도가 실제 성공 판정의 근거 — 넉넉한 재시도 예산
    check(s.count("ping -n") >= 2, "대기/재시도 양쪽에 ping 적용")
    # 회귀 방지: spawn_updater 가 .cmd 를 ascii 로 기록하므로 본문은 ascii 여야 함
    try:
        s.encode("ascii")
        check(True, "스크립트 본문이 ASCII 인코딩 가능(주석에 한글 금지)")
    except UnicodeEncodeError:
        check(False, "스크립트 본문이 ASCII 인코딩 가능(주석에 한글 금지)")


def test_child_env_scrubs_pyinstaller_vars():
    print("test_child_env_scrubs_pyinstaller_vars")
    base = {
        "PATH": "x", "NORMAL": "keep",
        "_MEIPASS2": r"C:\Temp\_MEI264082",
        "_MEI264082": "y",
        "_PYI_ARCHIVE_FILE": "z",
        "_PYI_APPLICATION_HOME_DIR": "w",
    }
    out = updater.child_env(base)
    check("_MEIPASS2" not in out, "_MEIPASS2 제거")
    check("_MEI264082" not in out, "_MEI* 제거")
    check("_PYI_ARCHIVE_FILE" not in out, "_PYI* 제거")
    check("_PYI_APPLICATION_HOME_DIR" not in out, "_PYI* 모두 제거")
    check(out.get("PATH") == "x" and out.get("NORMAL") == "keep", "일반 변수 보존")


def test_parallel_uploads_many_files():
    print("test_parallel_uploads_many_files (다수 파일 병렬 업로드)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; local.mkdir()
        n = 30
        for i in range(n):
            write(local / f"s{i:03d}.sav", f"DATA{i}")
        drive = FakeDrive()
        stats = sync_profile(drive, base_cfg(),
                             make_profile(local, Rules(include_extensions=[".sav"])),
                             max_workers=8)
        check(stats.uploaded_new == n, f"{n}개 전부 신규 업로드 집계")
        check(len(drive.files) == n, f"드라이브에 {n}개 모두 존재")
        check(not stats.errors, "오류 없음")
        # 내용 정합성(경합으로 뒤섞이지 않았는지)
        ok = all(drive.files[f"s{i:03d}.sav"]["bytes"] == f"DATA{i}".encode()
                 for i in range(n))
        check(ok, "각 파일 내용이 정확히 매칭(경합 없음)")


def test_parallel_mixed_directions():
    print("test_parallel_mixed_directions (병렬 양방향 신규)")
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / "local"; local.mkdir()
        for i in range(10):
            write(local / f"up{i}.sav", f"U{i}")       # 로컬에만 → 업로드
        drive = FakeDrive()
        for i in range(10):
            drive.files[f"dn{i}.sav"] = {"bytes": f"D{i}".encode(),
                                         "modified_epoch": time.time()}  # 드라이브에만 → 다운로드
        stats = sync_profile(drive, base_cfg(),
                             make_profile(local, Rules(include_extensions=[".sav"])),
                             max_workers=8)
        check(stats.uploaded_new == 10 and stats.downloaded_new == 10,
              "업로드 10 / 다운로드 10 집계")
        check(all((local / f"dn{i}.sav").exists() for i in range(10)),
              "드라이브 전용 10개 모두 로컬로 다운로드됨")
        check(all(drive.files.get(f"up{i}.sav") for i in range(10)),
              "로컬 전용 10개 모두 업로드됨")
        check(not stats.errors, "오류 없음")


def test_connect_falls_back_on_revoked_token():
    print("test_connect_falls_back_on_revoked_token")
    from unittest import mock
    from google.auth.exceptions import RefreshError
    from savesync import drive as drive_mod
    from savesync.drive import DriveClient, DriveError

    with tempfile.TemporaryDirectory() as d:
        tokfile = Path(d) / "token.json"
        tokfile.write_text('{"old":"revoked"}', encoding="utf-8")

        # 폐기된 토큰: 만료 + refresh_token 있음 + refresh() 가 invalid_grant
        stale = mock.Mock()
        stale.valid = False
        stale.expired = True
        stale.refresh_token = "revoked"
        stale.refresh.side_effect = RefreshError("invalid_grant")

        good = mock.Mock()
        good.to_json.return_value = '{"token":"new"}'
        flow = mock.Mock()
        flow.run_local_server.return_value = good

        with mock.patch.object(drive_mod.paths, "token_path", return_value=tokfile), \
             mock.patch.object(drive_mod.Credentials, "from_authorized_user_file",
                               return_value=stale), \
             mock.patch.object(drive_mod.oauth_client, "is_configured",
                               return_value=True), \
             mock.patch.object(drive_mod.oauth_client, "client_config",
                               return_value={"installed": {}}), \
             mock.patch.object(drive_mod.InstalledAppFlow, "from_client_config",
                               return_value=flow), \
             mock.patch.object(drive_mod, "build", return_value="SERVICE"):

            # run_auth_flow=True: 폐기 토큰을 버리고 브라우저 인증으로 폴백해야 함
            c = DriveClient()
            c.connect(run_auth_flow=True)
            check(stale.refresh.called, "폐기 토큰 refresh 시도함")
            check(flow.run_local_server.called, "RefreshError 후 새 인증으로 폴백")
            check(c.service == "SERVICE", "service 생성됨")
            check(tokfile.read_text(encoding="utf-8") == '{"token":"new"}',
                  "새 토큰으로 저장됨")

            # run_auth_flow=False: 폴백 금지 → DriveError (브라우저 안 열림)
            flow.run_local_server.reset_mock()
            c2 = DriveClient()
            raised = False
            try:
                c2.connect(run_auth_flow=False)
            except DriveError:
                raised = True
            check(raised, "run_auth_flow=False 면 폐기 토큰 시 DriveError")
            check(not flow.run_local_server.called, "백그라운드에선 브라우저 안 염")


def main():
    for t in [test_matcher, test_matcher_ext_and_glob,
              test_new_files_both_ways, test_conflict_newer_local_wins,
              test_conflict_newer_drive_wins, test_policy_force_local, test_subfolders,
              test_unset_local_folder_guard, test_sync_all_skips_unset_profiles,
              test_export_omits_local_folder, test_merge_by_name_preserves_local,
              test_merge_adds_new_with_empty_local, test_export_import_round_trip,
              test_parse_version, test_is_newer, test_pick_asset,
              test_render_update_script, test_child_env_scrubs_pyinstaller_vars,
              test_connect_falls_back_on_revoked_token,
              test_parallel_uploads_many_files, test_parallel_mixed_directions]:
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
