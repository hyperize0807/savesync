"""로깅 설정. 파일(회전) + 콘솔 + 메모리 버퍼(설정 창 로그 뷰용)."""
from __future__ import annotations

import logging
from collections import deque
from logging.handlers import RotatingFileHandler

from . import paths

_configured = False

# 설정 창 '로그' 탭에서 읽어가는 최근 로그 버퍼
_recent: deque[str] = deque(maxlen=500)


class _BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            _recent.append(self.format(record))
        except Exception:
            pass


def get_recent() -> list[str]:
    return list(_recent)


def setup() -> logging.Logger:
    global _configured
    logger = logging.getLogger("savesync")
    if _configured:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(str(paths.log_path()), maxBytes=512 * 1024,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    bh = _BufferHandler()
    bh.setFormatter(fmt)
    logger.addHandler(bh)

    _configured = True
    return logger
