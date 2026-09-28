"""Start VisionGuard at boot, keep it running, and log why it ever stopped.

Written for a machine that is switched on and then left alone. Three jobs:

    start     Always. Pre-flight is a report, not a gate. It used to refuse to launch
              when a check failed, which on a machine left alone means the one program
              that could have shown the fault is the program that is not running - the
              operator arrives to an empty desktop and no clue. The application already
              displays a missing camera or PLC and retries for ever; let it.

              It starts with --autostart, so monitoring begins by itself. Nobody has to
              walk over and press START after a power cut.

    restart   If it exits - a crash, a driver fault, someone closing the window by
              accident - it comes back, with a backoff so a boot loop cannot spin the CPU.
              Quitting on purpose (exit code 0) is respected and the launcher stops too.

    python tools/launcher.py                     # normal
    python tools/launcher.py --no-restart        # start once, do not supervise
    python tools/launcher.py --wait 120          # settle for up to 2 min first (optional)
    python tools/launcher.py --require-preflight # old behaviour: refuse to start on a fault
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    PROJECT = Path(sys.executable).resolve().parent
else:
    PROJECT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

LOG_DIR = PROJECT / "logs"
BACKOFF_S = [5, 10, 30, 60, 120, 300]        # grows, so a permanent fault cannot spin
CLEAN_EXIT = 0


def attach_parent_console() -> bool:
    """Borrow the console of whoever launched us, so --check-only can print its report.

    The launcher is built windowed (console=False) so that starting it at logon does not
    park a terminal on the operator's screen. Hiding the window instead does not work on
    Windows 11: the console is hosted by Windows Terminal, a separate process, so
    ShowWindow on our own console handle hides nothing anybody can see.

    Windowed costs us stdout, which only --check-only needs. Attaching to the parent's
    console gives it back when the report is run from a command prompt, which is the only
    way it is ever run.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
        for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
            if stream is None or getattr(stream, "closed", False):
                setattr(sys, name, open("CONOUT$", "w", encoding="utf-8", errors="replace"))
        return True
    except Exception:
        return False


def setup_log() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("launcher")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_DIR / "launcher.log", encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    # Only when there is somewhere to write. A windowed build has no stderr, and a
    # StreamHandler pointed at None throws on the first line it tries to log.
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    return log


def preflight(log: logging.Logger, wait_s: float) -> bool:
    """Report what is wrong, optionally waiting for the plant to wake up first.

    Returns whether everything passed. The caller starts the application either way
    unless --require-preflight says otherwise.
    """
    from visionguard.diagnostics.preflight import FAIL, run

    deadline = time.monotonic() + max(0.0, wait_s)
    attempt = 0
    while True:
        attempt += 1
        report = run()
        bad = [(c, d) for s, c, d in report.rows if s == FAIL]
        if not bad:
            warn = [c for s, c, _ in report.rows if s == "WARN"]
            log.info("Pre-flight passed on attempt %d%s", attempt,
                     f" (warnings: {', '.join(warn)})" if warn else "")
            return True
        names = ", ".join(c for c, _ in bad)
        if time.monotonic() >= deadline:
            log.warning("Pre-flight has failures: %s", names)
            for check, detail in bad:
                log.warning("    %s: %s", check, detail)
            return False
        log.warning("Pre-flight not ready (%s) - retrying in 15s", names)
        time.sleep(15)


def run_app(log: logging.Logger, extra: list) -> int:
    if FROZEN:
        cmd = [str(PROJECT / "VisionGuard.exe"), "--autostart", *extra]
    else:
        cmd = [sys.executable, str(PROJECT / "main.py"), "--autostart", *extra]
    log.info("Starting: %s", " ".join(cmd))
    started = time.monotonic()
    try:
        # The camera plugin only honours OPENCV_FFMPEG_CAPTURE_OPTIONS when the process is
        # born with it. Handing it over here saves the app relaunching itself to get it.
        from visionguard.utils.process_env import child_env
        env = child_env()
    except Exception:          # the app relaunches itself instead; slower, same result
        env = None
    try:
        code = subprocess.call(cmd, cwd=str(PROJECT), env=env)
    except Exception as exc:
        log.exception("Could not start VisionGuard: %s", exc)
        return -1
    log.info("VisionGuard exited with %d after %.0f minutes", code, (time.monotonic() - started) / 60)
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description="VisionGuard launcher / watchdog")
    # 0, not 180. Waiting only delays the screen the operator is waiting for: the
    # application handles a camera or PLC that is not there yet, and keeps retrying.
    ap.add_argument("--wait", type=float, default=0.0,
                    help="seconds to retry pre-flight before starting anyway (default 0)")
    ap.add_argument("--no-restart", action="store_true", help="do not supervise")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--require-preflight", action="store_true",
                    help="refuse to start while any check fails (old behaviour)")
    ap.add_argument("--check-only", action="store_true",
                    help="print the pre-flight report and exit, without starting anything")
    args, extra = ap.parse_known_args()

    if args.check_only:
        # This is where the report can actually be read; the application itself is
        # windowed and has nowhere to print.
        attach_parent_console()
        from visionguard.diagnostics.preflight import main as preflight_main
        return preflight_main("config")

    log = setup_log()
    log.info("=" * 60)
    log.info("Launcher starting (%s)", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if not args.skip_preflight and not preflight(log, args.wait):
        if args.require_preflight:
            log.error("--require-preflight is set, so not starting. Fix the failures above.")
            return 1
        # Starting anyway is the whole point. A site machine boots before the camera
        # switch does, and a PLC address that is not answering at 06:00 is exactly the
        # thing somebody needs to see on the screen at 06:01.
        log.warning("Starting anyway - the application will show these on screen and retry.")

    failures = 0
    while True:
        code = run_app(log, extra)
        if args.no_restart:
            return code
        if code == CLEAN_EXIT:
            log.info("Closed on purpose - the launcher stops too.")
            return 0
        delay = BACKOFF_S[min(failures, len(BACKOFF_S) - 1)]
        failures += 1
        log.warning("Restart %d in %ds", failures, delay)
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
