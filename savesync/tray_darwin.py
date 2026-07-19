"""macOS 전용 시스템 트레이(메뉴 막대) 앱.

Windows 구현(tray.py)은 메인 스레드의 숨은 Tk 루트가 모든 GUI 를 처리하고
pystray 를 별도 스레드에서 돌리지만, macOS 에서는 그 조합이 성립하지 않는다:

- AppKit UI(상태바 아이콘)는 반드시 메인 스레드에서 돌아야 하고,
- tkinter 와 pystray(PyObjC)가 한 프로세스에서 Cocoa 이벤트 루프를 공유하면
  메뉴 콜백 진입 시 GIL 상태가 깨져 크래시한다
  (Fatal Python error: PyEval_RestoreThread — pystray#138).

그래서 macOS 는 프로세스를 분리한다:

- 이 프로세스   : pystray 아이콘을 메인 스레드에서 run() (tkinter 를 전혀 안 씀)
- 설정 창       : `python -m savesync --settings` 서브프로세스로 실행
- 설정 반영     : config.json 의 mtime 이 바뀌면 다시 읽는다 (인증도 token.json
                  파일 기반이라 별도 전달이 필요 없다)
- 대화상자/경고 : osascript(외부 프로세스) — 충돌 '매번 물어보기' 포함
"""
from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import AppKit
import pystray
from PyObjCTools import AppHelper

from . import appicon
from . import config as config_mod
from . import logsetup, paths
from .config import CONFLICT_ASK
from .drive import DriveClient
from .scheduler import SyncScheduler


# ---------- osascript 유틸 (별도 프로세스 UI — GIL/AppKit 과 무관해 안전) ----------
def _osa_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _osa(script: str, timeout: float | None = None) -> str:
    """osascript 한 줄을 실행하고 stdout 을 돌려준다(실패/취소 시 빈 문자열)."""
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _alert(title: str, msg: str) -> None:
    _osa(f'display alert "{_osa_escape(title)}" message "{_osa_escape(msg)}"')


class TrayAppMac:
    def __init__(self):
        self.log = logsetup.setup()
        self.cfg = config_mod.load()
        self._cfg_mtime = self._config_file_mtime()
        self.drive = DriveClient()

        self._settings_proc: subprocess.Popen | None = None
        self._stopping = threading.Event()

        self.scheduler = SyncScheduler(
            drive=self.drive,
            get_config=self._current_config,
            log=self.log.info,
            on_cycle_done=self._on_cycle_done,
        )

        self.icon = pystray.Icon(
            "savesync",
            appicon.render(64),
            "SaveSync",
            menu=pystray.Menu(
                pystray.MenuItem("설정 열기", self._menu_open_settings, default=True),
                pystray.MenuItem("지금 동기화", self._menu_sync_now),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda item: f"마지막: {self._last_line()}",
                                 None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("종료", self._menu_quit),
            ),
        )
        self._last_summary = "아직 동기화 안 됨"
        self._last_time = ""
        # '폴더 미지정으로 건너뜀' 1회성 알림 추적 (tray.py 와 동일한 동작)
        self._notified_missing: set[str] = set()

    # ---------- 설정 재로드 ----------
    @staticmethod
    def _config_file_mtime() -> float:
        try:
            return paths.config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _current_config(self) -> config_mod.Config:
        """설정 창(별도 프로세스)이 저장한 변경을 mtime 기준으로 재로드한다."""
        m = self._config_file_mtime()
        if m != self._cfg_mtime:
            self._cfg_mtime = m
            self.cfg = config_mod.load()
            self.log.info("설정 변경 감지 — config.json 다시 읽음")
        return self.cfg

    # ---------- 상태 표시 ----------
    def _last_line(self) -> str:
        return f"[{self._last_time}] {self._last_summary}" if self._last_time else self._last_summary

    def _on_cycle_done(self, results):
        # (스케줄러 스레드에서 호출됨)
        active = [r for r in results if r.has_activity()]
        error_count = sum(len(r.errors) for r in results)

        if active:
            self._last_summary = " | ".join(f"{r.name}: {r.summary()}" for r in active)
        elif results:
            self._last_summary = "변경 없음"
        else:
            self._last_summary = "대상 프로필 없음"
        self._last_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # NSMenu 갱신은 AppKit 작업이므로 메인 스레드로 마샬링한다.
        try:
            AppHelper.callAfter(self.icon.update_menu)
        except Exception:
            pass
        # notify 는 darwin 백엔드가 osascript 로 구현되어 있어 어느 스레드든 안전.
        try:
            if error_count:
                err_names = ", ".join(r.name for r in results if r.errors)
                self.icon.notify(
                    f"오류 {error_count}건 ({err_names}) — 설정 > 로그 탭 확인",
                    "SaveSync 동기화 (오류 있음)",
                )
            elif active:
                body = "\n".join(f"{r.name}: {r.summary()}" for r in active)
                self.icon.notify(body, "SaveSync 동기화 완료")
        except Exception:
            pass

        self._notify_skipped_profiles()

    def _notify_skipped_profiles(self):
        """폴더 미지정으로 건너뛴 활성 프로필을 1회성 알림으로 알린다."""
        missing = {p.name for p in self.cfg.profiles
                   if p.enabled and not p.has_local_folder()}
        if not missing:
            self._notified_missing.clear()
            return
        if missing == self._notified_missing:
            return
        self._notified_missing = missing
        try:
            names = ", ".join(sorted(missing))
            self.icon.notify(
                f"로컬 세이브 폴더가 지정되지 않아 건너뜀: {names}\n"
                f"설정 > 프로필에서 폴더를 지정하세요.",
                "SaveSync — 동기화되지 않은 프로필",
            )
        except Exception:
            pass

    # ---------- 메뉴 콜백 (pystray 메인 루프에서 호출됨) ----------
    def _menu_open_settings(self, icon=None, item=None):
        # 이미 열려 있으면 그 창을 앞으로 가져온다.
        # (NSRunningApplication 은 System Events 와 달리 자동화 권한이 필요 없다)
        if self._settings_proc is not None and self._settings_proc.poll() is None:
            try:
                ra = AppKit.NSRunningApplication.\
                    runningApplicationWithProcessIdentifier_(self._settings_proc.pid)
                if ra is not None:
                    ra.activateWithOptions_(
                        AppKit.NSApplicationActivateIgnoringOtherApps)
            except Exception:
                pass
            return
        # tkinter 는 이 프로세스에서 쓸 수 없으므로 설정 창은 서브프로세스로 띄운다.
        # cwd 를 저장소 루트로 고정해 -m savesync 가 항상 임포트되게 한다.
        repo_root = str(Path(__file__).resolve().parent.parent)
        self._settings_proc = subprocess.Popen(
            [sys.executable, "-m", "savesync", "--settings"], cwd=repo_root)

    def _menu_sync_now(self, icon=None, item=None):
        # 메인 루프(메뉴 콜백)를 막지 않도록 워커 스레드에서 진행한다.
        threading.Thread(target=self._sync_now_worker, daemon=True).start()

    def _menu_quit(self, icon=None, item=None):
        try:
            self._stopping.set()
            self.scheduler.stop()
        finally:
            self.icon.stop()

    # ---------- 수동 동기화 (워커 스레드) ----------
    def _sync_now_worker(self):
        cfg = self._current_config()

        if self.scheduler.running_sync:
            try:
                self.icon.notify("이미 동기화가 진행 중입니다.", "SaveSync")
            except Exception:
                pass
            return

        missing = [p.name for p in cfg.profiles
                   if p.enabled and not p.has_local_folder()]
        if missing:
            _alert("동기화 중단",
                   "로컬 세이브 폴더가 지정되지 않은 프로필이 있습니다.\n"
                   f"'{', '.join(missing)}' 프로필의 로컬 세이브 폴더를 지정해주세요.")
            return

        resolver = None
        if cfg.conflict_policy == CONFLICT_ASK:
            resolver = self._conflict_resolver

        ok = self.scheduler.run_sync(on_conflict=resolver)
        if not ok:
            self.log.info("수동 동기화: 실행되지 않음(인증/프로필 확인)")

    def _conflict_resolver(self, rel: str, local_mtime: float, drive_mtime: float) -> str:
        """충돌 시 osascript 대화상자로 물어본다(동기화 스레드에서 호출됨)."""
        lt = datetime.fromtimestamp(local_mtime).strftime("%Y-%m-%d %H:%M:%S")
        dt = datetime.fromtimestamp(drive_mtime).strftime("%Y-%m-%d %H:%M:%S")
        newer = "로컬" if local_mtime >= drive_mtime else "드라이브"
        msg = (f"파일: {rel}\n\n"
               f"로컬 수정:     {lt}\n"
               f"드라이브 수정: {dt}\n"
               f"(더 최신: {newer})")
        out = _osa(
            f'display dialog "{_osa_escape(msg)}" with title "SaveSync 동기화 충돌" '
            'buttons {"건너뛰기", "드라이브 우선", "로컬 우선"} '
            f'default button "{newer} 우선" with icon caution')
        # 반환 예: "button returned:로컬 우선" (ESC/오류 시 빈 문자열 → 건너뛰기)
        if "로컬 우선" in out:
            return "local"
        if "드라이브 우선" in out:
            return "drive"
        return "skip"

    # ---------- 설정 프로세스의 '지금 동기화' 트리거 감시 ----------
    def _watch_sync_trigger(self):
        """설정 창(별도 프로세스)이 만드는 트리거 파일을 폴링해 수동 동기화를 실행."""
        trig = paths.sync_trigger_path()
        while not self._stopping.is_set():
            try:
                if trig.exists():
                    trig.unlink(missing_ok=True)
                    self._sync_now_worker()
            except Exception:
                pass
            self._stopping.wait(2.0)

    # ---------- 실행 ----------
    def run(self):
        # 이전 실행이 남긴 트리거가 있으면 시작하자마자 동기화되는 사고 방지
        paths.sync_trigger_path().unlink(missing_ok=True)
        threading.Thread(target=self._watch_sync_trigger,
                         name="SyncTriggerWatcher", daemon=True).start()
        self.scheduler.start()
        self.log.info("SaveSync 시작됨 (메뉴 막대 상주, macOS)")
        # pystray 를 메인 스레드에서 실행 — darwin 백엔드의 표준(지원) 모드.
        self.icon.run()


def main():
    TrayAppMac().run()
