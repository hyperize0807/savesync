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
    from . import config as config_mod, logsetup
    from .drive import DriveClient
    from .gui import SettingsWindow

    logsetup.setup()
    cfg = config_mod.load()
    drive = DriveClient()
    SettingsWindow(cfg, drive).show()  # 자체 Tk 루트 + mainloop
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

    from .tray import main as tray_main
    tray_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
