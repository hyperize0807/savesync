"""시스템 트레이 앱.

구조
----
- 메인 스레드: 숨겨진 Tk 루트의 mainloop 를 돌린다 (모든 GUI/대화상자를 여기서 처리).
- 트레이 아이콘: pystray 를 데몬 스레드에서 run().
- 트레이 메뉴 콜백은 root.after(0, ...) 로 Tk 스레드에 마샬링한다.
- 동기화는 SyncScheduler(별도 스레드)에서 실행.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox

import pystray
from PIL import Image, ImageDraw

from . import config as config_mod
from . import logsetup
from .config import CONFLICT_ASK
from .drive import DriveClient
from .gui import SettingsWindow
from .scheduler import SyncScheduler


def _make_icon_image() -> Image.Image:
    """간단한 클라우드+화살표 느낌의 16색 아이콘 생성."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 26, 38, 50), fill=(70, 130, 220, 255))
    d.ellipse((26, 20, 58, 48), fill=(70, 130, 220, 255))
    d.rectangle((14, 38, 50, 52), fill=(70, 130, 220, 255))
    # 위아래 화살표(동기화)
    d.polygon([(28, 30), (34, 30), (31, 24)], fill="white")
    d.polygon([(30, 44), (36, 44), (33, 50)], fill="white")
    d.rectangle((30, 30, 32, 45), fill="white")
    d.rectangle((32, 30, 34, 45), fill="white")
    return img


class TrayApp:
    def __init__(self):
        self.log = logsetup.setup()
        self.cfg = config_mod.load()
        self.drive = DriveClient()

        # GUI 마샬링용 숨은 루트
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("SaveSync")

        self._settings: SettingsWindow | None = None
        self._ui_queue: queue.Queue = queue.Queue()

        self.scheduler = SyncScheduler(
            drive=self.drive,
            get_config=lambda: self.cfg,
            log=self._log_and_notify,
            on_cycle_done=self._on_cycle_done,
        )

        self.icon = pystray.Icon(
            "savesync",
            _make_icon_image(),
            "SaveSync",
            menu=pystray.Menu(
                pystray.MenuItem("설정 열기", self._menu_open_settings, default=True),
                pystray.MenuItem("지금 동기화", self._menu_sync_now),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda item: f"마지막: {self._last_status()}",
                                 None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("종료", self._menu_quit),
            ),
        )
        self._last_summary = "아직 동기화 안 됨"

    # ---------- 로깅/알림 ----------
    def _log_and_notify(self, msg: str):
        self.log.info(msg)

    def _last_status(self) -> str:
        return self._last_summary

    def _on_cycle_done(self, results):
        parts = [r.summary() for r in results] or ["대상 프로필 없음"]
        self._last_summary = " | ".join(parts)
        error_count = sum(len(r.errors) for r in results)
        try:
            self.icon.update_menu()
            if error_count:
                self.icon.notify(
                    f"오류 {error_count}건 발생 — 설정 > 로그 탭 확인\n{self._last_summary}",
                    "SaveSync 동기화 (오류 있음)",
                )
            else:
                self.icon.notify(self._last_summary, "SaveSync 동기화 완료")
        except Exception:
            pass
        # 설정 창이 열려 있으면 결과/로그 갱신
        if self._settings is not None:
            try:
                self._settings.last_result_summary = self._last_summary
                self.root.after(0, self._settings._refresh_log)
            except Exception:
                pass

    # ---------- 메뉴 콜백 (트레이 스레드 → Tk 스레드로 마샬) ----------
    def _menu_open_settings(self, icon=None, item=None):
        self.root.after(0, self._open_settings)

    def _menu_sync_now(self, icon=None, item=None):
        self.root.after(0, self._sync_now)

    def _menu_quit(self, icon=None, item=None):
        self.root.after(0, self._quit)

    # ---------- 실제 동작 (Tk 스레드) ----------
    def _open_settings(self):
        if self._settings and tk.Toplevel.winfo_exists(self._settings.root):
            self._settings.root.lift()
            self._settings.root.focus_force()
            return
        self._settings = SettingsWindow(
            self.cfg, self.drive,
            on_save=self._on_settings_saved,
            on_sync_now=self._sync_now,
            master=self.root,
            last_result_summary=self._last_summary,
        )
        self._settings.show()

    def _on_settings_saved(self, cfg):
        # 설정 저장 시 cfg 객체만 갱신한다. 변경된 동기화 주기는 다음 타이머
        # 대기 계산 시 get_config() 를 통해 자동 반영된다(즉시 동기화는 하지 않음).
        self.cfg = cfg

    def _sync_now(self):
        if self.scheduler.running_sync:
            messagebox.showinfo("동기화", "이미 동기화가 진행 중입니다.")
            return

        resolver = None
        if self.cfg.conflict_policy == CONFLICT_ASK:
            resolver = self._tk_conflict_resolver

        def work():
            ok = self.scheduler.run_sync(on_conflict=resolver)
            if not ok:
                self.root.after(0, lambda: self.log.info("수동 동기화: 실행되지 않음(인증/프로필 확인)"))
        threading.Thread(target=work, daemon=True).start()

    def _tk_conflict_resolver(self, rel: str, local_mtime: float, drive_mtime: float) -> str:
        """충돌 시 Tk 대화상자로 사용자에게 물어본다(다른 스레드에서 호출됨)."""
        import datetime as _dt
        result_q: queue.Queue = queue.Queue()

        def ask():
            lt = _dt.datetime.fromtimestamp(local_mtime).strftime("%Y-%m-%d %H:%M:%S")
            dt = _dt.datetime.fromtimestamp(drive_mtime).strftime("%Y-%m-%d %H:%M:%S")
            newer = "로컬" if local_mtime >= drive_mtime else "드라이브"
            ans = messagebox.askyesnocancel(
                "동기화 충돌",
                f"파일: {rel}\n\n"
                f"로컬 수정:     {lt}\n"
                f"드라이브 수정: {dt}\n"
                f"(더 최신: {newer})\n\n"
                f"[예] 로컬 → 드라이브 로 덮어쓰기\n"
                f"[아니오] 드라이브 → 로컬 로 덮어쓰기\n"
                f"[취소] 이 파일 건너뛰기",
            )
            if ans is True:
                result_q.put("local")
            elif ans is False:
                result_q.put("drive")
            else:
                result_q.put("skip")

        self.root.after(0, ask)
        return result_q.get()  # 사용자가 응답할 때까지 대기

    def _quit(self):
        try:
            self.scheduler.stop()
        finally:
            self.icon.stop()
            self.root.quit()

    # ---------- 실행 ----------
    def run(self):
        self.scheduler.start()
        # 트레이 아이콘은 별도 스레드에서
        threading.Thread(target=self.icon.run, name="TrayIcon", daemon=True).start()
        self.log.info("SaveSync 시작됨 (트레이 상주)")
        self.root.mainloop()


def main():
    TrayApp().run()
