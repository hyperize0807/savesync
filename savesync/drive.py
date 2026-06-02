"""Google Drive API 래퍼.

인증: Google Cloud Console에서 발급한 "데스크톱 앱" OAuth 클라이언트
      (credentials.json)를 %APPDATA%\\SaveSync 에 두면, 최초 1회 브라우저
      인증 후 token.json 에 토큰을 저장한다.

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

from . import paths

# 임의의 사용자 폴더를 읽고/쓰려면 전체 drive 스코프가 필요하다.
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


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
        return paths.credentials_path().exists()

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
                if not self.has_credentials():
                    raise DriveError(
                        "credentials.json 이 없습니다. Google Cloud Console에서 "
                        "데스크톱 OAuth 클라이언트를 만들어 "
                        f"{paths.credentials_path()} 에 저장하세요."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(paths.credentials_path()), SCOPES
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
    def resolve_folder(self, url_or_id: str) -> tuple[str, str]:
        """드라이브 폴더 URL 또는 ID 를 받아 (folder_id, name) 반환."""
        self._ensure()
        fid = url_or_id.strip()
        # URL 에서 ID 추출
        if "/folders/" in fid:
            fid = fid.split("/folders/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        elif "id=" in fid:
            fid = fid.split("id=", 1)[1].split("&", 1)[0]
        meta = self.service.files().get(
            fileId=fid, fields="id,name,mimeType", supportsAllDrives=True
        ).execute()
        if meta.get("mimeType") != FOLDER_MIME:
            raise DriveError("지정한 항목이 폴더가 아닙니다.")
        return meta["id"], meta["name"]

    def list_folders(self, parent_id: str = "root") -> list[dict[str, str]]:
        """parent 아래의 하위 폴더 목록(폴더 선택 UI용)."""
        self._ensure()
        q = (f"'{parent_id}' in parents and mimeType='{FOLDER_MIME}' "
             f"and trashed=false")
        res = self.service.files().list(
            q=q, fields="files(id,name)", orderBy="name",
            pageSize=1000, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        return res.get("files", [])

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
                meta = {"name": part, "mimeType": FOLDER_MIME, "parents": [parent]}
                created = self.service.files().create(
                    body=meta, fields="id", supportsAllDrives=True
                ).execute()
                child = created["id"]
                self._folder_cache[(parent, part)] = child
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
