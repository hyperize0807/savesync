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

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

from . import oauth_client, paths

# drive.file: 앱이 만든/연 파일만 접근 (사용자 동의·검수 부담이 작다).
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
# 내 드라이브 최상위에 만드는 앱 전용 루트 폴더 이름.
APP_ROOT_FOLDER = "SaveSync"


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
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif run_auth_flow:
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

    def _ensure(self):
        if self.service is None:
            raise DriveError("연결되지 않았습니다. connect() 를 먼저 호출하세요.")

    # ---------- 폴더 ----------
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

    def get_modified_epoch(self, file_id: str) -> float:
        self._ensure()
        meta = self.service.files().get(
            fileId=file_id, fields="modifiedTime", supportsAllDrives=True
        ).execute()
        return rfc3339_to_epoch(meta["modifiedTime"])
