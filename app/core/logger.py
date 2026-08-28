"""统一日志。"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from typing import Any

from app.core.config import settings

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-26s | %(message)s"
_configured = False

#: 最近日志环形缓冲，供 Web 控制台读取
LOG_BUFFER: deque[dict[str, Any]] = deque(maxlen=1000)


class BufferHandler(logging.Handler):
    """把日志写入内存环形缓冲。"""

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # pragma: no cover - 日志缓冲不应影响主流程
            LOG_BUFFER.append(
                {
                    "time": self.formatter.formatTime(record) if self.formatter else "",
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )


def setup_logging() -> None:
    """初始化根日志（幂等）。"""
    global _configured
    if _configured:
        return

    formatter = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    buffer_handler = BufferHandler()
    buffer_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.LOG_LEVEL.upper())
    root.addHandler(stream)
    root.addHandler(file_handler)
    root.addHandler(buffer_handler)

    for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取 logger，自动完成初始化。"""
    setup_logging()
    return logging.getLogger(name)


def recent_logs(limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
    """返回最近日志（按时间正序）。"""
    items = list(LOG_BUFFER)
    if level:
        wanted = level.upper()
        items = [item for item in items if item["level"] == wanted]
    return items[-limit:]
