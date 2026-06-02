"""상주 타이머.

별도 스레드에서 interval_minutes 마다 sync_all 을 실행한다.
trigger_now() 로 즉시 1회 실행을 요청할 수 있고, 실행 중 중복 실행은 막는다.
"""
from __future__ import annotations

import threading
from typing import Callable, TYPE_CHECKING

from . import config as config_mod
from .syncengine import sync_all

if TYPE_CHECKING:
    from .drive import DriveClient


class SyncScheduler:
    def __init__(
        self,
        drive: DriveClient,
        get_config: Callable[[], config_mod.Config],
        log: Callable[[str], None] = lambda m: None,
        on_cycle_done: Callable[[list], None] | None = None,
    ):
        self.drive = drive
        self.get_config = get_config
        self.log = log
        self.on_cycle_done = on_cycle_done

        self._wake = threading.Event()      # 타이머 대기 깨우기
        self._stop = threading.Event()
        self._run_lock = threading.Lock()   # 동기화 중복 실행 방지
        self._thread: threading.Thread | None = None
        self.last_result: list | None = None
        self.running_sync = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="SyncScheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger_now(self) -> None:
        """다음 사이클까지 기다리지 않고 즉시 (백그라운드 방식으로) 동기화."""
        self._wake.set()

    def run_sync(self, on_conflict=None) -> bool:
        """즉시 1회 동기화. 충돌 해결 콜백을 넘길 수 있다(수동 동기화용).

        이미 진행 중이면 False 를 반환한다. 호출 스레드에서 동기적으로 실행된다.
        """
        return self._run_once(on_conflict=on_conflict)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._run_once()
            interval = max(1, self.get_config().interval_minutes)
            # interval 분 동안 대기하되, trigger/stop 이 오면 즉시 깨어남
            self._wake.wait(timeout=interval * 60)
            self._wake.clear()

    def _run_once(self, on_conflict=None) -> bool:
        if self._stop.is_set():
            return False
        if not self._run_lock.acquire(blocking=False):
            self.log("이미 동기화가 진행 중입니다. 이번 트리거는 건너뜁니다.")
            return False
        try:
            self.running_sync = True
            if not self.drive.is_authorized():
                self.log("아직 Google Drive 인증이 안 되어 동기화를 건너뜁니다.")
                return False
            if self.drive.service is None:
                self.drive.connect(run_auth_flow=False)
            cfg = self.get_config()
            if not any(p.enabled for p in cfg.profiles):
                self.log("활성화된 프로필이 없습니다.")
                return False
            self.log("동기화 시작…")
            results = sync_all(self.drive, cfg, log=self.log, on_conflict=on_conflict)
            self.last_result = results
            if self.on_cycle_done:
                self.on_cycle_done(results)
            return True
        except Exception as e:
            self.log(f"동기화 실패: {e}")
            return False
        finally:
            self.running_sync = False
            self._run_lock.release()
