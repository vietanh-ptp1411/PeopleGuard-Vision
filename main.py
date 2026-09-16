"""VisionGuard - entry point.

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

from visionguard.utils.logger import setup_logging  # noqa: E402


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        logging.getLogger("SYSTEM").critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook


def main() -> int:
    parser = argparse.ArgumentParser(description="VisionGuard")
    parser.add_argument("--log-level", default=None, help="DEBUG / INFO / WARNING")
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()

    from visionguard.config.config_manager import ConfigManager

    cm = ConfigManager(args.config_dir)
    settings = cm.load_all()
    level_name = (args.log_level or settings.app.log_level or "INFO").upper()
    setup_logging("logs", getattr(logging, level_name, logging.INFO))
    _install_excepthook()
    log = logging.getLogger("SYSTEM")
    log.info("=" * 60)
    log.info("VisionGuard starting (python %s)", sys.version.split()[0])

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from visionguard.app.main_window import MainWindow
    from visionguard.app.theme import apply_theme
    from visionguard.app.wheel_guard import install as install_wheel_guard

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("VisionGuard")
    app.setOrganizationName("VisionGuard")
    apply_theme(app)
    install_wheel_guard(app)   # scrolling a settings page must never change a value

    window = MainWindow(cm)
    window.showMaximized()   # industrial HMI: always start on the full screen (F11 = borderless)
    code = app.exec()
    log.info("VisionGuard exited (%d)", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
