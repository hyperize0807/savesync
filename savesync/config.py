"""설정 로드/저장.

설정은 JSON 한 파일(%APPDATA%\\SaveSync\\config.json)에 보관한다.
여러 게임을 동기화할 수 있도록 "profiles" 리스트 구조를 사용한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

from . import paths

# 충돌(양쪽 모두 존재 + 내용 다름) 시 처리 정책
CONFLICT_NEWER = "newer"   # 수정날짜가 더 최신인 쪽을 채택 (기본)
CONFLICT_LOCAL = "local"   # 항상 로컬 → 드라이브
CONFLICT_DRIVE = "drive"   # 항상 드라이브 → 로컬
CONFLICT_ASK = "ask"       # 사용자에게 물어봄 (수동 동기화 시에만)

# 프로필 목록을 내보낼 때 Google Drive 의 SaveSync 루트에 저장하는 파일 이름.
PROFILES_BLOB_NAME = "savesync-profiles.json"


@dataclass
class Rules:
    """동기화 대상 파일 규칙."""
    include_extensions: list[str] = field(default_factory=list)  # 예: [".sav", ".dat"]
    include_globs: list[str] = field(default_factory=list)       # 예: ["save*.*"]
    exclude_globs: list[str] = field(default_factory=list)       # 예: ["*.tmp", "*.log"]
    recursive: bool = True                                       # 하위 폴더 포함 여부

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Rules":
        return Rules(
            include_extensions=[e.lower() for e in d.get("include_extensions", [])],
            include_globs=list(d.get("include_globs", [])),
            exclude_globs=list(d.get("exclude_globs", [])),
            recursive=bool(d.get("recursive", True)),
        )


@dataclass
class Profile:
    """게임 하나에 대한 동기화 설정."""
    name: str = "기본 프로필"
    local_folder: str = ""
    drive_folder_id: str = ""
    drive_folder_name: str = ""
    rules: Rules = field(default_factory=Rules)
    enabled: bool = True

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Profile":
        return Profile(
            name=d.get("name", "기본 프로필"),
            local_folder=d.get("local_folder", ""),
            drive_folder_id=d.get("drive_folder_id", ""),
            drive_folder_name=d.get("drive_folder_name", ""),
            rules=Rules.from_dict(d.get("rules", {})),
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class Config:
    profiles: list[Profile] = field(default_factory=list)
    conflict_policy: str = CONFLICT_NEWER
    auto_sync_enabled: bool = True   # 주기 자동 동기화 사용 여부 (끄면 수동만)
    interval_minutes: int = 60
    backup_enabled: bool = True
    backup_dir: str = ""
    mtime_tolerance_seconds: int = 2  # 양쪽 mtime 차이가 이 이하면 같은 파일로 간주

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Config":
        return Config(
            profiles=[Profile.from_dict(p) for p in d.get("profiles", [])],
            conflict_policy=d.get("conflict_policy", CONFLICT_NEWER),
            auto_sync_enabled=bool(d.get("auto_sync_enabled", True)),
            interval_minutes=int(d.get("interval_minutes", 60)),
            backup_enabled=bool(d.get("backup_enabled", True)),
            backup_dir=d.get("backup_dir", ""),
            mtime_tolerance_seconds=int(d.get("mtime_tolerance_seconds", 2)),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def export_profiles_payload(cfg: Config) -> dict[str, Any]:
    """프로필 목록을 이식 가능한 형태로 직렬화한다.

    기기별 경로인 local_folder 는 제외하고, name/drive_folder_name/
    drive_folder_id/enabled/rules 만 내보낸다. drive_folder_id 는 계정 단위로
    모든 기기에서 동일하므로 함께 저장해 정확히 같은 폴더에 바인딩되게 한다.
    """
    profiles = []
    for p in cfg.profiles:
        d = asdict(p)
        d.pop("local_folder", None)
        profiles.append(d)
    return {"version": 1, "profiles": profiles}


def merge_imported_profiles(cfg: Config, payload: dict[str, Any]) -> tuple[int, int]:
    """가져온 페이로드를 cfg.profiles 에 이름 기준으로 병합한다.

    - 같은 이름 프로필: drive_folder_name/drive_folder_id/rules/enabled 갱신,
      local_folder 는 보존 → updated
    - 없던 프로필: 그대로 추가(local_folder 는 빈 값) → added

    (added, updated) 개수를 반환하고 cfg.profiles 를 in-place 로 수정한다.
    """
    by_name = {p.name: p for p in cfg.profiles}
    added = updated = 0
    for d in payload.get("profiles", []):
        incoming = Profile.from_dict(d)
        existing = by_name.get(incoming.name)
        if existing is not None:
            existing.drive_folder_name = incoming.drive_folder_name
            existing.drive_folder_id = incoming.drive_folder_id
            existing.rules = incoming.rules
            existing.enabled = incoming.enabled
            updated += 1
        else:
            incoming.local_folder = ""
            cfg.profiles.append(incoming)
            by_name[incoming.name] = incoming
            added += 1
    return added, updated


def load() -> Config:
    p = paths.config_path()
    if not p.exists():
        cfg = Config()
        cfg.backup_dir = str(paths.default_backup_dir())
        save(cfg)
        return cfg
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = Config.from_dict(data)
    if not cfg.backup_dir:
        cfg.backup_dir = str(paths.default_backup_dir())
    return cfg


def save(cfg: Config) -> None:
    p = paths.config_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
