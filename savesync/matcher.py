"""동기화 대상 파일 규칙 매칭.

규칙 우선순위:
1) exclude_globs 에 걸리면 제외
2) include_extensions / include_globs 중 하나라도 매치하면 포함
3) include_extensions 와 include_globs 가 모두 비어 있으면 "모든 파일 포함"
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterator

from .config import Rules


def matches(rel_path: str, rules: Rules) -> bool:
    """rel_path(상대경로, 파일명 포함)가 규칙에 맞으면 True."""
    name = os.path.basename(rel_path)
    name_lower = name.lower()

    # 1) 제외 규칙
    for pat in rules.exclude_globs:
        if fnmatch.fnmatch(name_lower, pat.lower()):
            return False

    has_include = bool(rules.include_extensions) or bool(rules.include_globs)
    if not has_include:
        # 포함 규칙이 없으면 (제외되지 않은) 모든 파일 포함
        return True

    # 2) 확장자
    ext = os.path.splitext(name_lower)[1]
    if ext in rules.include_extensions:
        return True

    # 3) glob 패턴
    for pat in rules.include_globs:
        if fnmatch.fnmatch(name_lower, pat.lower()):
            return True

    return False


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
