"""동기화 대상 파일 규칙 매칭.

규칙 우선순위:
1) exclude_globs 에 걸리면 제외
2) 지정된 포함 조건(확장자·패턴)을 "모두" 만족해야 포함(AND).
   - 확장자 목록 안에서는 하나라도 맞으면 됨(OR), 패턴 목록 안에서도 OR.
   - 단, 확장자와 패턴을 둘 다 입력했다면 양쪽 모두 만족해야 함.
     예) 확장자 '.sav' + 패턴 'G3P_II*' → 'G3P_II_01.sav' 포함,
         'G30001.sav'(확장자만 일치)는 제외.
   - 한쪽만 입력했다면 그 조건만 적용.
3) include_extensions 와 include_globs 가 모두 비어 있으면 "모든 파일 포함"
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterator

from .config import Rules

# OS가 만드는 메타데이터 파일 — 세이브가 아니므로 규칙과 무관하게 항상 제외.
# (macOS: .DS_Store, AppleDouble '._*' / Windows: Thumbs.db, desktop.ini)
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
JUNK_PREFIXES = ("._",)


def matches(rel_path: str, rules: Rules) -> bool:
    """rel_path(상대경로, 파일명 포함)가 규칙에 맞으면 True."""
    name = os.path.basename(rel_path)
    name_lower = name.lower()

    # 0) OS 메타데이터 파일은 무조건 제외
    if name_lower in JUNK_NAMES or name_lower.startswith(JUNK_PREFIXES):
        return False

    # 1) 제외 규칙
    for pat in rules.exclude_globs:
        if fnmatch.fnmatch(name_lower, pat.lower()):
            return False

    has_ext = bool(rules.include_extensions)
    has_glob = bool(rules.include_globs)
    if not has_ext and not has_glob:
        # 포함 규칙이 없으면 (제외되지 않은) 모든 파일 포함
        return True

    # 2) 확장자 조건(입력된 경우 반드시 만족)
    if has_ext:
        ext = os.path.splitext(name_lower)[1]
        if ext not in rules.include_extensions:
            return False

    # 3) glob 패턴 조건(입력된 경우 반드시 만족)
    if has_glob:
        if not any(fnmatch.fnmatch(name_lower, pat.lower())
                   for pat in rules.include_globs):
            return False

    return True


def iter_local_files(local_folder: str, rules: Rules) -> Iterator[str]:
    """규칙에 맞는 로컬 파일들의 상대경로(POSIX 구분자)를 yield."""
    root = Path(local_folder)
    if not root.is_dir():
        return
    if rules.recursive:
        walker = os.walk(root)
    else:
        # 최상위만
        files = [f for f in os.listdir(root) if (root / f).is_file()]
        walker = [(str(root), [], files)]

    for dirpath, _dirnames, filenames in walker:
        for fn in filenames:
            abs_path = Path(dirpath) / fn
            rel = abs_path.relative_to(root).as_posix()
            if matches(rel, rules):
                yield rel
