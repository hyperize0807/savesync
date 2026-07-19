"""진입점.

사용법:
  python -m savesync                # 트레이 상주 모드(기본)
  python -m savesync --sync-once    # 헤드리스로 1회 동기화 후 종료
  python -m savesync --settings     # 설정 창만 열기
"""
from __future__ import annotations

import argparse
import sys


def _sync_once() -> int:
    from . import config as config_mod, logsetup
    from .drive import DriveClient
    from .syncengine import sync_all

    log = logsetup.setup()
    cfg = config_mod.load()
    drive = DriveClient()
    if not drive.is_authorized():
        log.error("Google Drive 인증이 필요합니다. 먼저 트레이 앱 '계정' 탭에서 연결하세요.")
        return 2
    drive.connect(run_auth_flow=False)
    results = sync_all(drive, cfg, log=log.info)
    errors = sum(len(r.errors) for r in results)
    return 1 if errors else 0


def _settings_only() -> int:
    from . import config as config_mod, logsetup, paths
    from .drive import DriveClient
    from .gui import SettingsWindow

    logsetup.setup()
    cfg = config_mod.load()
    drive = DriveClient()

    on_sync_now = None
    if sys.platform == "darwin":
        # macOS 의 설정 창은 트레이와 별도 프로세스다. '지금 동기화'는 트리거
        # 파일로 트레이 프로세스에 요청한다 (tray_darwin.py 가 2초마다 감시).
        def on_sync_now() -> bool:
            paths.sync_trigger_path().touch()
            return True

    SettingsWindow(cfg, drive, on_sync_now=on_sync_now).show()  # 자체 Tk 루트 + mainloop
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="savesync")
    parser.add_argument("--sync-once", action="store_true", help="1회 동기화 후 종료")
    parser.add_argument("--settings", action="store_true", help="설정 창만 열기")
    args = parser.parse_args()

    if args.sync_once:
        return _sync_once()
    if args.settings:
        return _settings_only()

    if sys.platform == "darwin":
        # macOS 는 tkinter+pystray 를 한 프로세스에서 쓸 수 없어 전용 구현 사용
        from .tray_darwin import main as tray_main
    else:
        from .tray import main as tray_main
    tray_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
