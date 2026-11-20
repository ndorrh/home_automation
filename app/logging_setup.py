"""Structured logging setup with rotating file output and Windows-safe truncation."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_HANDLER_LOCK = threading.Lock()
_FILE_HANDLER: RotatingFileHandler | None = None


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """Configure root logger with console and rotating file handlers."""
    global _FILE_HANDLER
    log_dir.mkdir(parents=True, exist_ok=True)
    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger("smart_home_av")
    root.setLevel(log_level)

    if root.handlers:
        return root

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "server.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    with _HANDLER_LOCK:
        _FILE_HANDLER = file_handler

    root.addHandler(file_handler)
    return root


def clear_log_file(log_dir: Path) -> bool:
    """Safely truncate server.log in-place even while RotatingFileHandler holds it open."""
    with _HANDLER_LOCK:
        handler = _FILE_HANDLER
    if handler is not None:
        handler.acquire()
        try:
            if handler.stream is not None:
                handler.stream.seek(0)
                handler.stream.truncate(0)
                handler.flush()
                return True
        finally:
            handler.release()

    log_path = log_dir / "server.log"
    if log_path.exists():
        log_path.write_text("", encoding="utf-8")
        return True
    return False
