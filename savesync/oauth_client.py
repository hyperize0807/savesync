"""앱에 내장되는 공유 Google OAuth 클라이언트.

일반 사용자는 더 이상 자기 credentials.json 을 만들 필요가 없다.
앱 제작자가 Google Cloud Console에서 "데스크톱 앱" OAuth 클라이언트를
딱 1개 만들고, 그 client_id / client_secret 을 아래에 채워 배포한다.

데스크톱(installed) 앱의 client_secret 은 진짜 기밀이 아니다 — Google 도
설치형 앱에서는 비밀로 취급할 수 없다고 문서화하고 있어, 앱에 포함해
배포하는 것이 정상 패턴이다.

값을 공개 저장소에 커밋하기 꺼려지면, 코드 상수는 비워 두고 아래 중
하나로 주입할 수 있다(우선순위 순):
  1) 환경변수  SAVESYNC_OAUTH_CLIENT_ID / SAVESYNC_OAUTH_CLIENT_SECRET
  2) 파일      %APPDATA%\\SaveSync\\oauth_client.json  ({"installed": {...}} 형식)
  3) 아래 상수 CLIENT_ID / CLIENT_SECRET
"""
from __future__ import annotations

import json
import os

from . import paths

# 앱 제작자가 채운다(또는 환경변수/JSON 으로 주입).
CLIENT_ID = ""
CLIENT_SECRET = ""

_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _from_env() -> tuple[str, str] | None:
    cid = os.environ.get("SAVESYNC_OAUTH_CLIENT_ID")
    csec = os.environ.get("SAVESYNC_OAUTH_CLIENT_SECRET")
    if cid and csec:
        return cid, csec
    return None


def _from_file() -> tuple[str, str] | None:
    p = paths.app_dir() / "oauth_client.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    node = data.get("installed", data)
    cid, csec = node.get("client_id"), node.get("client_secret")
    if cid and csec:
        return cid, csec
    return None


def _resolve() -> tuple[str, str] | None:
    if CLIENT_ID and CLIENT_SECRET:
        embedded = (CLIENT_ID, CLIENT_SECRET)
    else:
        embedded = None
    return _from_env() or _from_file() or embedded


def is_configured() -> bool:
    """앱이 사용자를 인증시킬 수 있는 상태인지(클라이언트가 채워졌는지)."""
    return _resolve() is not None


def client_config() -> dict:
    """InstalledAppFlow.from_client_config 에 넘길 client config dict."""
    resolved = _resolve()
    if not resolved:
        raise RuntimeError(
            "Google OAuth 클라이언트가 설정되지 않았습니다. "
            "oauth_client.py 를 채우거나 환경변수/oauth_client.json 으로 주입하세요."
        )
    cid, csec = resolved
    return {
        "installed": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": _AUTH_URI,
            "token_uri": _TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }
