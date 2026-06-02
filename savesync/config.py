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
    interval_minutes: int = 60
    backup_enabled: bool = True
    backup_dir: str = ""
    mtime_tolerance_seconds: int = 2  # 양쪽 mtime 차이가 이 이하면 같은 파일로 간주

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Config":
        return Config(
            profiles=[Profile.from_dict(p) for p in d.get("profiles", [])],
            conflict_policy=d.get("conflict_policy", CONFLICT_NEWER),
            interval_minutes=int(d.get("interval_minutes", 60)),
            backup_enabled=bool(d.get("backup_enabled", True)),
            backup_dir=d.get("backup_dir", ""),
            mtime_tolerance_seconds=int(d.get("mtime_tolerance_seconds", 2)),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


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
