"""덮어쓰기 전 백업.

덮어쓰기 대상이 되는 (곧 사라질) 버전을 백업 폴더에 보관한다.
구조:  <backup_dir>/<profile>/<YYYYMMDD-HHMMSS>/<side>/<상대경로>
  - side="local" : 로컬 파일이 곧 드라이브 버전으로 덮어쓰여질 때 보관한 로컬 원본
  - side="drive" : 드라이브 파일이 곧 로컬 버전으로 덮어쓰여질 때 내려받아 보관한 드라이브 원본
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def _safe(name: str) -> str:
    keep = "-_. ()[]"
    return "".join(c if c.isalnum() or c in keep else "_" for c in name).strip() or "profile"


class BackupSession:
    """한 번의 동기화 실행 동안 같은 타임스탬프 폴더를 공유한다."""

    def __init__(self, backup_root: str, profile_name: str, enabled: bool = True):
        self.enabled = enabled
        self.count = 0
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_dir = Path(backup_root) / _safe(profile_name) / stamp

    def backup_local(self, local_file: Path, rel_path: str) -> Path | None:
        """덮어쓰기 직전의 로컬 파일을 백업."""
        if not self.enabled or not local_file.exists():
            return None
        dest = self.session_dir / "local" / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file, dest)
        self.count += 1
        return dest

    def backup_drive_bytes(self, data: bytes, rel_path: str) -> Path | None:
        """덮어쓰기 직전의 드라이브 파일(미리 내려받은 바이트)을 백업."""
        if not self.enabled:
            return None
        dest = self.session_dir / "drive" / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        self.count += 1
        return dest
