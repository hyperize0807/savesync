"""Google Drive API 래퍼.

인증: 앱에 내장된 공유 OAuth 클라이언트(oauth_client.py)로 최초 1회 브라우저
      인증을 진행하고 token.json 에 토큰을 저장한다. 사용자가 직접
      credentials.json 을 만들 필요가 없다.

스코프: drive.file — "앱이 만들었거나 사용자가 앱으로 연 파일"만 접근한다.
      따라서 임의의 기존 폴더를 탐색/선택할 수는 없고, 대신 앱이 내 드라이브
      최상위에 'SaveSync' 폴더를 만들고 그 아래 프로필별 폴더에 동기화한다.

핵심 설계
---------
- 업로드 시 파일의 modifiedTime 을 로컬 파일의 mtime 으로 설정한다.
  이렇게 해야 양쪽의 "수정한 날짜"를 동일 기준으로 비교할 수 있고,
  업로드 시각 때문에 매번 재동기화되는 루프를 막을 수 있다.
- 폴더 계층(하위 폴더)을 드라이브에 그대로 미러링하며, 폴더 ID를 캐시한다.
"""
from __future__ import annotations

import functools
import http.client
import io
import logging
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

from . import oauth_client, paths

_log = logging.getLogger("savesync")

# drive.file: 앱이 만든/연 파일만 접근 (사용자 동의·검수 부담이 작다).
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
# 내 드라이브 최상위에 만드는 앱 전용 루트 폴더 이름.
APP_ROOT_FOLDER = "SaveSync"

# 한 번 만든 service(=httplib2 연결)를 오래 재사용하면, 자동 동기화 주기(기본 1시간)
# 사이의 긴 유휴 시간 동안 방화벽/백신/원격 서버가 keep-alive 소켓을 끊어버린다.
# 다음 호출에서 죽은 소켓을 재사용하다 ConnectionAbortedError(WinError 10053) 등이
# 난다. 이때는 인증이 아니라 '연결'이 끊긴 것이므로, service 를 새로 만들어
# (필요하면 토큰도 갱신해) 재시도하면 복구된다.
_TRANSIENT_NET_ERRORS = (
    ConnectionError,            # ConnectionAborted/Reset/BrokenPipe(WinError 10053/10054) 포함
    TimeoutError,
    socket.timeout,
    ssl.SSLError,
    http.client.BadStatusLine,
    http.client.IncompleteRead,
    http.client.ResponseNotReady,
    http.client.CannotSendRequest,
)
# 재시도 총 횟수(최초 1회 + 재시도 N-1회).
_MAX_ATTEMPTS = 3


def _retry_on_connection(method):
    """API 메서드를 감싸, 일시적 연결 끊김이면 재연결 후 재시도한다.

    메서드 본문이 매번 self.service 로부터 요청을 새로 만들기 때문에, 재연결로
    self.service 가 교체되면 다음 시도는 새 연결을 사용한다.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return method(self, *args, **kwargs)
            except _TRANSIENT_NET_ERRORS as e:
                if attempt >= _MAX_ATTEMPTS - 1:
                    raise
                _log.info(
                    f"Drive 연결 끊김 — 재연결 후 재시도 "
                    f"({attempt + 1}/{_MAX_ATTEMPTS - 1}): {e}"
                )
                try:
                    self._reconnect()
                except Exception as re:  # 재연결 실패(예: 토큰 폐기) → 원래 예외 전파
                    _log.info(f"자동 재연결 실패: {re}")
                time.sleep(min(2 ** attempt, 5))
        return method(self, *args, **kwargs)  # 이론상 도달하지 않음
    return wrapper


def rfc3339_to_epoch(s: str) -> float:
    """Drive 의 modifiedTime(RFC3339, 예: 2024-01-02T03:04:05.678Z) → epoch 초."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def epoch_to_rfc3339(epoch: float) -> str:
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond/1000):03d}Z"


class DriveError(Exception):
    pass


class DriveClient:
    def __init__(self):
        self.service = None
        self._folder_cache: dict[tuple[str, str], str] = {}  # (parent_id, name) -> id

    # ---------- 인증 ----------
    @staticmethod
    def has_credentials() -> bool:
        """앱에 OAuth 클라이언트가 설정되어 사용자가 연결할 수 있는 상태인지."""
        return oauth_client.is_configured()

    @staticmethod
    def is_authorized() -> bool:
        return paths.token_path().exists()

    def connect(self, run_auth_flow: bool = True) -> None:
        """저장된 토큰으로 연결. 없거나 만료되면 (허용 시) 브라우저 인증."""
        creds: Credentials | None = None
        tok = paths.token_path()
        if tok.exists():
            creds = Credentials.from_authorized_user_file(str(tok), SCOPES)

        if not creds or not creds.valid:
            refreshed = False
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    refreshed = True
                except RefreshError:
                    # 리프레시 토큰이 폐기/만료됨(invalid_grant 등). 죽은 토큰을
                    # 버리고, 허용되면 새 브라우저 인증으로 폴백한다. 이렇게 해야
                    # '재인증' 버튼만으로 자가 복구된다(폐기된 토큰이 깔려 있어도).
                    creds = None
            if not refreshed:
                if run_auth_flow:
                    if not oauth_client.is_configured():
                        raise DriveError(
                            "앱에 Google OAuth 클라이언트가 설정되어 있지 않습니다. "
                            "(개발자) oauth_client.py 를 채우거나 환경변수/"
                            f"{paths.app_dir() / 'oauth_client.json'} 로 주입하세요."
                        )
                    flow = InstalledAppFlow.from_client_config(
                        oauth_client.client_config(), SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                else:
                    raise DriveError("인증되지 않았습니다.")
            with open(tok, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _reconnect(self) -> None:
        """끊긴 연결을 새 service 로 교체한다(필요 시 토큰 자동 갱신).

        브라우저 인증 흐름은 절대 열지 않는다(백그라운드 스레드에서 호출되므로).
        저장된 refresh 토큰이 폐기/만료됐다면 connect 가 예외를 던지고, 그 경우
        호출자(재시도 데코레이터)는 원래의 연결 예외를 그대로 전파한다.
        """
        self.connect(run_auth_flow=False)

    def _ensure(self):
        if self.service is None:
            raise DriveError("연결되지 않았습니다. connect() 를 먼저 호출하세요.")

    # ---------- 폴더 ----------
    @_retry_on_connection
    def _create_folder(self, name: str, parent_id: str) -> str:
        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        created = self.service.files().create(
            body=meta, fields="id", supportsAllDrives=True
        ).execute()
        self._folder_cache[(parent_id, name)] = created["id"]
        return created["id"]

    def _ensure_app_root(self) -> str:
        """내 드라이브 최상위에 'SaveSync' 폴더를 찾거나 만든다."""
        return (self._find_child_folder("root", APP_ROOT_FOLDER)
                or self._create_folder(APP_ROOT_FOLDER, "root"))

    def ensure_profile_folder(self, profile_folder_name: str) -> str:
        """'SaveSync/<이름>' 하위 폴더를 찾거나 만들어 그 ID 를 반환한다.

        drive.file 스코프에서는 임의의 기존 폴더에 접근할 수 없으므로,
        앱이 만든 이 폴더 트리에만 동기화한다(다른 기기의 같은 앱은 동일
        클라이언트라 이 폴더를 다시 찾을 수 있어 기기 간 동기화가 된다).
        """
        self._ensure()
        app_root = self._ensure_app_root()
        name = (profile_folder_name or "").strip() or "기본"
        return self._find_child_folder(app_root, name) or self._create_folder(name, app_root)

    @_retry_on_connection
    def _find_child_folder(self, parent_id: str, name: str) -> str | None:
        key = (parent_id, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        q = (f"'{parent_id}' in parents and mimeType='{FOLDER_MIME}' "
             f"and name='{name.replace(chr(39), chr(92)+chr(39))}' and trashed=false")
        res = self.service.files().list(
            q=q, fields="files(id,name)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = res.get("files", [])
        if files:
            self._folder_cache[key] = files[0]["id"]
            return files[0]["id"]
        return None

    def ensure_folder_path(self, root_id: str, rel_dir: str) -> str:
        """root 아래에 rel_dir(예: 'a/b') 폴더 계층을 생성/확인하고 마지막 ID 반환."""
        self._ensure()
        parent = root_id
        if not rel_dir or rel_dir in (".", ""):
            return parent
        for part in rel_dir.split("/"):
            if not part:
                continue
            child = self._find_child_folder(parent, part)
            if child is None:
                child = self._create_folder(part, parent)
            parent = child
        return parent

    # ---------- 파일 목록 ----------
    @_retry_on_connection
    def list_files_recursive(self, root_id: str) -> dict[str, dict[str, Any]]:
        """root 폴더 아래의 모든 (폴더 아닌) 파일을 상대경로 → 메타 로 반환.

        반환 메타: {id, modified_epoch, size}
        """
        self._ensure()
        result: dict[str, dict[str, Any]] = {}
        # (folder_id, rel_prefix) 큐로 BFS
        queue: list[tuple[str, str]] = [(root_id, "")]
        while queue:
            folder_id, prefix = queue.pop()
            page_token = None
            while True:
                res = self.service.files().list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="nextPageToken, files(id,name,mimeType,modifiedTime,size)",
                    pageSize=1000, pageToken=page_token,
                    supportsAllDrives=True, includeItemsFromAllDrives=True,
                ).execute()
                for f in res.get("files", []):
                    rel = f"{prefix}{f['name']}" if not prefix else f"{prefix}/{f['name']}"
                    if f["mimeType"] == FOLDER_MIME:
                        queue.append((f["id"], rel))
                    else:
                        result[rel] = {
                            "id": f["id"],
                            "modified_epoch": rfc3339_to_epoch(f["modifiedTime"]),
                            "size": int(f.get("size", 0)),
                        }
                page_token = res.get("nextPageToken")
                if not page_token:
                    break
        return result

    # ---------- 업로드 / 다운로드 ----------
    @_retry_on_connection
    def upload_new(self, root_id: str, rel_path: str, local_file: Path) -> str:
        """새 파일 업로드. modifiedTime 을 로컬 mtime 으로 설정."""
        self._ensure()
        rel_dir = "/".join(rel_path.split("/")[:-1])
        name = rel_path.split("/")[-1]
        parent = self.ensure_folder_path(root_id, rel_dir)
        mtime = local_file.stat().st_mtime
        body = {
            "name": name,
            "parents": [parent],
            "modifiedTime": epoch_to_rfc3339(mtime),
        }
        media = MediaFileUpload(str(local_file), resumable=True)
        created = self.service.files().create(
            body=body, media_body=media, fields="id", supportsAllDrives=True
        ).execute()
        return created["id"]

    @_retry_on_connection
    def update_existing(self, file_id: str, local_file: Path) -> None:
        """기존 드라이브 파일을 로컬 내용으로 덮어쓰기. modifiedTime 도 동기화."""
        self._ensure()
        mtime = local_file.stat().st_mtime
        media = MediaFileUpload(str(local_file), resumable=True)
        self.service.files().update(
            fileId=file_id,
            body={"modifiedTime": epoch_to_rfc3339(mtime)},
            media_body=media,
            supportsAllDrives=True,
        ).execute()

    @_retry_on_connection
    def download_bytes(self, file_id: str) -> bytes:
        """파일 내용을 메모리로 다운로드."""
        self._ensure()
        req = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        return buf.getvalue()

    @_retry_on_connection
    def get_modified_epoch(self, file_id: str) -> float:
        self._ensure()
        meta = self.service.files().get(
            fileId=file_id, fields="modifiedTime", supportsAllDrives=True
        ).execute()
        return rfc3339_to_epoch(meta["modifiedTime"])

    # ---------- SaveSync 루트의 메타 파일(JSON) ----------
    @_retry_on_connection
    def _find_child_file(self, parent_id: str, name: str) -> str | None:
        """parent 아래에서 폴더가 아닌 파일을 이름으로 찾아 ID 반환(없으면 None)."""
        q = (f"'{parent_id}' in parents and mimeType!='{FOLDER_MIME}' "
             f"and name='{name.replace(chr(39), chr(92)+chr(39))}' and trashed=false")
        res = self.service.files().list(
            q=q, fields="files(id,name)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def download_root_text(self, filename: str) -> str | None:
        """SaveSync 루트의 텍스트 파일을 읽어 문자열로 반환(없으면 None)."""
        self._ensure()
        root = self._ensure_app_root()
        fid = self._find_child_file(root, filename)
        if fid is None:
            return None
        return self.download_bytes(fid).decode("utf-8")

    @_retry_on_connection
    def upload_root_text(self, filename: str, text: str,
                         mime: str = "application/json") -> None:
        """SaveSync 루트에 텍스트 파일을 만들거나 덮어쓴다(메모리 업로드)."""
        self._ensure()
        root = self._ensure_app_root()
        media = MediaIoBaseUpload(
            io.BytesIO(text.encode("utf-8")), mimetype=mime, resumable=True)
        fid = self._find_child_file(root, filename)
        if fid is None:
            self.service.files().create(
                body={"name": filename, "parents": [root]},
                media_body=media, fields="id", supportsAllDrives=True,
            ).execute()
        else:
            self.service.files().update(
                fileId=fid, media_body=media, supportsAllDrives=True,
            ).execute()
