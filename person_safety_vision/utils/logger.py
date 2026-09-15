"""Logging setup: console + daily file (logs/YYYY-MM-DD.log) + optional Qt bridge.

Module loggers used across the app are short, uppercase names so the log is easy
to scan on an industrial PC:  CAMERA, AI, ROI, PLC, SYSTEM, UI, STORAGE.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-8s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_CONFIGURED = False


class DailyFileHandler(logging.handlers.TimedRotatingFileHandler):
    """File handler that always writes to logs/YYYY-MM-DD.log (rotates at midnight)."""

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        filename = self.log_dir / f"{datetime.now():%Y-%m-%d}.log"
        super().__init__(str(filename), when="midnight", backupCount=60, encoding=encoding, utc=False)

    def doRollover(self) -> None:  # noqa: N802 (logging API)
        # Instead of renaming the old file, just open a new file for the new date.
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        self.baseFilename = os.path.abspath(str(self.log_dir / f"{datetime.now():%Y-%m-%d}.log"))
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(datetime.now().timestamp()))


def setup_logging(log_dir: Path | str = "logs", level: int = logging.INFO, console: bool = True) -> logging.Logger:
    """Configure the root logger once. Safe to call multiple times."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(level)
        return root
    root.setLevel(level)
    fmt = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)

    try:
        fh = DailyFileHandler(Path(log_dir))
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:  # read-only disk etc. Never crash because of logging.
        root.warning("Cannot create log file in %s: %s", log_dir, exc)

    # Quiet noisy third-party loggers.
    for noisy in ("ultralytics", "urllib3", "PIL", "matplotlib", "torch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Convenience loggers
camera_log = logging.getLogger("CAMERA")
ai_log = logging.getLogger("AI")
roi_log = logging.getLogger("ROI")
plc_log = logging.getLogger("PLC")
system_log = logging.getLogger("SYSTEM")
ui_log = logging.getLogger("UI")
storage_log = logging.getLogger("STORAGE")


def try_import_qt_handler() -> Optional[type]:
    """Return QtLogHandler class if PySide6 is available (UI log panel bridge)."""
    try:
        from PySide6.QtCore import QObject, Signal
    except Exception:  # pragma: no cover - headless environments
        return None

    class _Emitter(QObject):
        message = Signal(str, int)  # formatted text, level number

    class QtLogHandler(logging.Handler):
        """Forwards log records to the UI thread through a Qt signal (thread-safe)."""

        def __init__(self) -> None:
            super().__init__()
            self.emitter = _Emitter()
            self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-8s | %(message)s", "%H:%M:%S"))

        def emit(self, record: logging.LogRecord) -> None:
            try:
                self.emitter.message.emit(self.format(record), record.levelno)
            except RuntimeError:
                pass  # emitter deleted during shutdown

    return QtLogHandler
