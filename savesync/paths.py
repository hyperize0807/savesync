"""애플리케이션 데이터 경로 정의.

설정/토큰/백업/로그는 모두 %APPDATA%\\SaveSync 아래에 보관한다.
"""
from __future__ import annotations

import os
from pathlib import Path


def app_dir() -> Path:
    """%APPDATA%\\SaveSync (Windows). 그 외 OS는 ~/.savesync."""
    base = os.environ.get("APPDATA")
    if base:
        root = Path(base) / "SaveSync"
    else:
        root = Path.home() / ".savesync"
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    return app_dir() / "config.json"


def credentials_path() -> Path:
    """사용자가 Google Cloud Console에서 발급받은 OAuth 클라이언트 파일."""
    return app_dir() / "credentials.json"


def token_path() -> Path:
    """OAuth 인증 후 저장되는 토큰."""
    return app_dir() / "token.json"


def default_backup_dir() -> Path:
    d = app_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return app_dir() / "savesync.log"


def sync_trigger_path() -> Path:
    """'지금 동기화' 요청 트리거 파일 (macOS: 설정 프로세스 → 트레이 프로세스).

    macOS 는 설정 창이 별도 프로세스라(tray_darwin.py 참고) 파일로 신호를 보낸다.
    """
    return app_dir() / "sync-now.trigger"
