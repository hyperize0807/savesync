"""인앱 자동 업데이트.

GitHub Releases 의 최신 SaveSync.exe 와 현재 버전을 비교하고, 새 버전이 있으면
내려받아 실행 중인 exe 를 교체한 뒤 재시작한다.

핵심 제약
---------
- `--onefile` exe 는 실행 중 자기 자신을 덮어쓸 수 없다. 따라서 헬퍼 `.cmd` 를
  작성해 detached 로 띄우고, 그 스크립트가 "현재 프로세스 종료 대기 → 교체 →
  재실행 → 자기 삭제" 를 수행한다.
- 프로즌 exe 에서 stdlib urllib 의 HTTPS 인증서 검증이 동작하도록 certifi 의
  CA 번들을 사용한다(검증 비활성 폴백은 절대 하지 않는다 — 실행파일을 받는
  채널이므로 보안상 위험).

순수 함수(parse_version/is_newer/pick_asset/render_update_script/is_frozen/
exe_path)는 네트워크 없이 단위 테스트가 가능하다.
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import __version__, paths

OWNER_REPO = "hyperize0807/savesync"
RELEASES_API = f"https://api.github.com/repos/{OWNER_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{OWNER_REPO}/releases/latest"
ASSET_NAME = "SaveSync.exe"
_USER_AGENT = f"SaveSync/{__version__}"


# ----------------------------- 순수 함수 -----------------------------
def parse_version(tag: str) -> tuple[int, int, int, int]:
    """'v1.2.3' / '1.2.3' / 'v1.2.3-rc1' 을 비교 가능한 키로 변환한다.

    반환: (major, minor, patch, release_rank).
    release_rank 는 정식 릴리스가 1, 프리릴리스(-, + 메타 존재)가 0 이라
    같은 코어에서 '1.0.2' > '1.0.2-rc1' 이 된다. 예외를 던지지 않는다.
    """
    s = (tag or "").strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    # 프리릴리스/빌드 메타 분리
    rank = 1
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
            rank = 0
    parts = s.split(".")
    nums = []
    for i in range(3):
        try:
            nums.append(int(parts[i]))
        except (IndexError, ValueError):
            nums.append(0)
    return (nums[0], nums[1], nums[2], rank)


def is_newer(latest_tag: str, current: str) -> bool:
    """latest_tag 가 current 보다 높은 버전이면 True.

    latest 파싱이 의심스러우면(모두 0) False 를 돌려 안전하게 동작한다.
    """
    latest = parse_version(latest_tag)
    if latest[:3] == (0, 0, 0):
        return False
    return latest > parse_version(current)


def pick_asset(assets: list[dict[str, Any]], name: str = ASSET_NAME) -> dict | None:
    """릴리스 자산 목록에서 지정 이름의 자산을 찾는다(없으면 None)."""
    for a in assets or []:
        if a.get("name") == name:
            return a
    return None


def is_frozen() -> bool:
    """PyInstaller 등으로 패키징된 exe 로 실행 중인지."""
    return bool(getattr(sys, "frozen", False))


def exe_path() -> Path:
    """현재 실행 파일 경로(프로즌일 때 SaveSync.exe)."""
    return Path(sys.executable)


def render_update_script() -> str:
    """교체용 헬퍼 .cmd 본문을 만든다.

    인자: %~1=PID, %~2="기존 exe", %~3="새 exe".
    값을 본문에 인터폴레이션하지 않고 argv 로만 받으므로 공백/특수문자에 안전하다.

    주의점(과거 버그)
    -----------------
    - 대기에는 `timeout` 을 쓰지 않는다. 헬퍼는 stdin 이 리다이렉트된(DEVNULL)
      detached 프로세스로 실행되는데, 그 경우 `timeout` 은 "Input redirection is
      not supported" 로 즉시 종료되어 대기가 전혀 일어나지 않는다.
      대신 `ping -n N 127.0.0.1` 으로 대기한다(콘솔 입력이 필요 없음).
    - `--onefile` exe 는 부트로더(부모)와 실제 앱(자식) 두 프로세스가 모두
      SaveSync.exe 를 잠근다. PID 한 개만 기다리면 파일이 아직 잠겨 있을 수 있으므로,
      실제 성공 판정은 `move` 재시도 루프가 한다(잠금이 풀려야만 move 가 성공).
    """
    return (
        "@echo off\r\n"
        "setlocal enableextensions disabledelayedexpansion\r\n"
        'set "PID=%~1"\r\n'
        'set "OLD=%~2"\r\n'
        'set "NEW=%~3"\r\n'
        "rem 1) best-effort wait for the caller PID to exit (ping = ~1s sleep)\r\n"
        "set /a tries=0\r\n"
        ":waitloop\r\n"
        'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul\r\n'
        "if errorlevel 1 goto replace\r\n"
        "set /a tries+=1\r\n"
        "if %tries% GEQ 30 goto replace\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto waitloop\r\n"
        "rem 2) retry move until the exe lock is released (onefile bootloader may linger)\r\n"
        ":replace\r\n"
        "set /a rtries=0\r\n"
        ":reploop\r\n"
        'move /y "%NEW%" "%OLD%" >nul 2>&1\r\n'
        "if not errorlevel 1 goto launch\r\n"
        "set /a rtries+=1\r\n"
        "if %rtries% GEQ 60 goto failed\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto reploop\r\n"
        ":launch\r\n"
        'start "" "%OLD%"\r\n'
        "goto cleanup\r\n"
        ":failed\r\n"
        'echo SaveSync update failed to replace the executable. > "%~dp0update_failed.txt"\r\n'
        'echo New build is at: %NEW% >> "%~dp0update_failed.txt"\r\n'
        'start "" "%OLD%"\r\n'
        ":cleanup\r\n"
        '(goto) 2>nul & del "%~f0"\r\n'
    )


# ----------------------------- 부수효과 -----------------------------
def _update_dir() -> Path:
    d = paths.app_dir() / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_ssl_context() -> ssl.SSLContext:
    """certifi 의 CA 번들로 검증 컨텍스트를 만든다(없으면 기본 컨텍스트)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        # 검증을 끄지는 않는다 — 기본 컨텍스트만 사용.
        return ssl.create_default_context()


def get_latest_release(timeout: float = 15.0) -> dict[str, Any]:
    """GitHub 최신 릴리스 정보를 조회한다.

    반환: {"tag", "exe_url", "size", "html_url"} (exe 자산이 없으면 exe_url=None)
    """
    req = urllib.request.Request(
        RELEASES_API,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    ctx = _make_ssl_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    asset = pick_asset(data.get("assets", []))
    return {
        "tag": data.get("tag_name", ""),
        "exe_url": asset.get("browser_download_url") if asset else None,
        "size": int(asset.get("size", 0)) if asset else 0,
        "html_url": data.get("html_url", RELEASES_PAGE),
    }


def download_asset(url: str, dest: Path | None = None,
                   expected_size: int | None = None,
                   progress: Callable[[int, int], None] | None = None,
                   timeout: float = 30.0) -> Path:
    """자산을 스트리밍으로 내려받는다. dest 가 없으면 update 폴더에 저장."""
    if dest is None:
        dest = _update_dir() / "SaveSync.new.exe"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    ctx = _make_ssl_context()
    written = 0
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        total = expected_size or int(resp.headers.get("Content-Length", 0) or 0)
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if progress:
                    progress(written, total)
    if written < 1024 * 1024:  # 1MiB 미만이면 비정상(exe 약 45MB)
        raise RuntimeError(f"다운로드가 비정상적으로 작습니다({written} bytes).")
    if expected_size and written != expected_size:
        raise RuntimeError(
            f"다운로드 크기 불일치(받음 {written} / 기대 {expected_size}).")
    return dest


class UpdateNotWritableError(RuntimeError):
    """exe 가 있는 폴더에 쓸 수 없어 교체할 수 없는 경우."""


def _dir_writable(d: Path) -> bool:
    """실제 임시파일을 만들어 쓰기 가능 여부를 확인한다(UAC 가상화 회피)."""
    probe = d / ".savesync_write_test"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except Exception:
        return False


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """재시작될 exe 에 물려줄 깨끗한 환경을 만든다.

    PyInstaller onefile 부트로더는 추출 폴더를 `_MEIPASS2` 등 내부 환경변수로
    자식에 전달한다(부모가 풀고 자식이 그 경로를 재사용). 이 변수가 헬퍼 cmd →
    start → 새 exe 로 상속되면, 새 부트로더가 재추출을 건너뛰고 **이미 삭제된 옛
    `_MEIxxxxxx` 폴더**에서 python3xx.dll 을 찾으려다 "Failed to load Python DLL"
    로 실패한다. 따라서 `_MEI*`/`_PYI*` 로 시작하는 부트로더 내부 변수를 제거한다.
    """
    src = os.environ if base is None else base
    return {k: v for k, v in src.items()
            if not (k.startswith("_MEI") or k.startswith("_PYI"))}


def spawn_updater(new_exe: Path, exe: Path | None = None) -> None:
    """헬퍼 .cmd 를 작성하고 detached 로 실행한다(교체+재시작은 .cmd 가 수행)."""
    target = Path(exe) if exe else exe_path()
    if not _dir_writable(target.parent):
        raise UpdateNotWritableError(str(target.parent))

    script = _update_dir() / "savesync_update.cmd"
    # newline="" 로 기록한다. Path.write_text/텍스트 모드는 문자열의 '\n' 을 다시
    # '\r\n' 으로 변환하므로, 본문의 '\r\n' 이 '\r\r\n'(이중 CR)이 되어 배치의
    # 라벨/goto/변수값을 오염시킨다. 본문이 이미 CRLF 를 포함하므로 변환을 끈다.
    with open(script, "w", encoding="ascii", newline="") as f:
        f.write(render_update_script())

    # CREATE_NO_WINDOW: 보이는 콘솔 창 없이, 그러나 콘솔 하위 명령(tasklist/ping 등)이
    #   정상 동작하는 (숨은) 콘솔을 부여한다. DETACHED_PROCESS 는 콘솔이 아예 없어
    #   콘솔 프로그램이 별도 터미널 창을 띄우는 문제가 있었다.
    # CREATE_NEW_PROCESS_GROUP: 부모(SaveSync.exe) 종료 후에도 헬퍼가 살아남도록 분리.
    creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # env 에서 PyInstaller 부트로더 변수를 제거해, 재시작된 새 exe 가 옛(삭제된)
    # _MEI 폴더를 재사용하려다 실패하는 문제를 막는다.
    subprocess.Popen(
        ["cmd", "/c", str(script), str(os.getpid()), str(target), str(new_exe)],
        creationflags=creationflags, close_fds=True, cwd=str(script.parent),
        env=child_env(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
