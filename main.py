"""PeopleGuard Vision - entry point.

    python main.py                 # normal start
    python main.py --log-level DEBUG
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Run relative to the project folder so models/, config/, logs/, events/ resolve predictably.
PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(PROJECT_DIR)
sys.path.insert(0, str(PROJECT_DIR))

# Reduce OpenCV/FFmpeg console noise and prefer TCP for RTSP unless overridden later by config.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

from person_safety_vision.utils.logger import setup_logging  # noqa: E402


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        logging.getLogger("SYSTEM").critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook


def main() -> int:
    parser = argparse.ArgumentParser(description="PeopleGuard Vision")
    parser.add_argument("--log-level", default=None, help="DEBUG / INFO / WARNING")
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()

    from person_safety_vision.config.config_manager import ConfigManager

    cm = ConfigManager(args.config_dir)
    settings = cm.load_all()
    level_name = (args.log_level or settings.app.log_level or "INFO").upper()
    setup_logging("logs", getattr(logging, level_name, logging.INFO))
    _install_excepthook()
    log = logging.getLogger("SYSTEM")
    log.info("=" * 60)
    log.info("PeopleGuard Vision starting (python %s)", sys.version.split()[0])

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from person_safety_vision.app.main_window import MainWindow
    from person_safety_vision.app.theme import apply_theme

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("PeopleGuard Vision")
    app.setOrganizationName("PeopleGuard")
    apply_theme(app)

    window = MainWindow(cm)
    window.showMaximized()   # industrial HMI: always start on the full screen (F11 = borderless)
    code = app.exec()
    log.info("PeopleGuard Vision exited (%d)", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
