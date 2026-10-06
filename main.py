"""VisionGuard - entry point.

    python main.py                 # normal start
    python main.py --log-level DEBUG
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_T0 = time.perf_counter()   # process start, near enough: the start-up log lines count from here

# Run relative to the application folder so models/, config/, logs/, events/ resolve
# predictably: beside the exe when frozen, beside this file when run from source.
if getattr(sys, "frozen", False):
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    PROJECT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(PROJECT_DIR))
os.chdir(PROJECT_DIR)

# The OpenCV FFmpeg options (TCP, short stream probe) cannot be set from inside this
# process - see visionguard/utils/process_env.py for what was tried. main() relaunches
# once with them in the environment unless the launcher already provided them.
from visionguard.utils import process_env  # noqa: E402
from visionguard.utils.logger import setup_logging  # noqa: E402

# There used to be a "prewarm" thread here that imported torch a second time, in parallel
# with the inference worker importing it for the model load. Two threads importing the
# same package serialise on the import lock, so it bought nothing, and both held the GIL
# against the window being built. The inference worker alone does the import now, started
# from SystemController.__init__ (see the note there on why before the window, not after).


def _single_instance_guard():
    """Refuse to be the second copy. Returns the lock, or None if one is already running.

    Auto-start makes this necessary rather than tidy. The scheduled task opens the
    application at logon, and then somebody double-clicks the desktop icon out of habit -
    now two processes fight over one RTSP session and, far worse, BOTH write to the PLC.
    Two writers on the same person bit is a safety interface nobody can reason about.

    The handle has to be kept alive by the caller; Windows releases it when the process
    ends, including when it crashes, so a stale lock cannot block the next start.
    """
    from PySide6.QtCore import QSharedMemory

    lock = QSharedMemory("VisionGuard-single-instance")
    if lock.attach():            # someone else created it; let go of our view of it
        lock.detach()
        return None
    return lock if lock.create(1) else None


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        logging.getLogger("SYSTEM").critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook


def main() -> int:
    parser = argparse.ArgumentParser(description="VisionGuard")
    parser.add_argument("--log-level", default=None, help="DEBUG / INFO / WARNING")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--autostart", action="store_true",
                        help="begin monitoring as soon as the window is up; also settable "
                             "as app.autostart in config/app_config.json")
    parser.add_argument("--check", action="store_true",
                        help="run the pre-flight check and exit (0 ready, 1 fault, 2 warnings)")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not process_env.inherited():
        # Started by a shortcut or a terminal, not by the launcher: come back with the
        # environment the camera plugin needs. Costs one interpreter start-up.
        return process_env.relaunch(__file__)

    if args.check:
        from visionguard.diagnostics.preflight import main as preflight_main
        return preflight_main(args.config_dir)

    from visionguard.config.config_manager import ConfigManager

    cm = ConfigManager(args.config_dir)
    settings = cm.load_all()
    if args.smoke_test and (not settings.plc.simulation_mode or settings.camera.is_ai_camera
                           or settings.app.autostart or settings.ai.detector.auto_load_on_start
                           or args.autostart):
        parser.error("Smoke test requires isolated config: simulated PLC, PC AI, autostart/model preload off")
    level_name = (args.log_level or settings.app.log_level or "INFO").upper()
    log_directory = Path(args.config_dir).resolve().parent / "logs" if args.smoke_test else "logs"
    setup_logging(log_directory, getattr(logging, level_name, logging.INFO))
    _install_excepthook()
    log = logging.getLogger("SYSTEM")
    log.info("=" * 60)
    log.info("VisionGuard starting (python %s) - %.1fs after launch", sys.version.split()[0], time.perf_counter() - _T0)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from visionguard.app.main_window import MainWindow
    from visionguard.app.theme import apply_theme
    from visionguard.app.wheel_guard import install as install_wheel_guard

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("VisionGuard")
    app.setOrganizationName("VisionGuard")

    guard = _single_instance_guard()
    if guard is None:
        log.warning("Another VisionGuard is already running - not starting a second one")
        if args.autostart:
            # Started by the machine, not by a person: there is nobody at the screen to
            # dismiss a dialog, and one left sitting there covers the live view until
            # somebody walks over. Two start mechanisms racing at boot is a normal,
            # healthy outcome - the loser should lose silently.
            return 0
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "VisionGuard",
                                "VisionGuard đã chạy sẵn rồi.\n\n"
                                "Phần mềm tự khởi động cùng Windows, nên thường nó đã "
                                "chạy sẵn khi bạn bấm lối tắt.")
        return 0

    apply_theme(app)
    install_wheel_guard(app)   # scrolling a settings page must never change a value

    window = MainWindow(cm)
    log.info("Window built %.1fs after launch", time.perf_counter() - _T0)
    window.showMaximized()   # industrial HMI: always start on the full screen (F11 = borderless)
    app.processEvents()      # paint it now, before autostart queues its work behind the first frame
    log.info("Window shown %.1fs after launch", time.perf_counter() - _T0)
    if args.smoke_test:
        from PySide6.QtCore import QTimer
        log.info("SMOKE TEST: isolated startup and graceful shutdown")
        QTimer.singleShot(3000, window.close)
    if args.autostart or settings.app.autostart:
        # Unattended site: nobody is there to press START after a power cut. The
        # controller waits for the model rather than for a fixed number of seconds.
        window.ctrl.autostart()
    code = app.exec()
    log.info("VisionGuard exited (%d)", code)
    if args.smoke_test:
        import json
        marker = Path(args.config_dir) / "smoke-result.json"
        marker.write_text(json.dumps({"graceful_exit": True, "exit_code": code}), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
