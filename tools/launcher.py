"""Start VisionGuard at boot, keep it running, and log why it ever stopped.

Written for a machine that is switched on and then left alone. Three jobs:

    wait      At boot the PC is ready long before the switch, the camera and the PLC are.
              Pre-flight is retried for a few minutes rather than failing once and giving
              up; only a fault that waiting cannot fix stops the launch.

    start     The application is started with --autostart, so monitoring begins by itself.
              Nobody has to walk over and press START after a power cut.

    restart   If it exits - a crash, a driver fault, someone closing the window by
              accident - it comes back, with a backoff so a boot loop cannot spin the CPU.
              Quitting on purpose (exit code 0) is respected and the launcher stops too.

    python tools/launcher.py               # normal
    python tools/launcher.py --no-restart  # start once, do not supervise
    python tools/launcher.py --wait 300    # how long to wait for cameras/PLC at boot
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


def setup_log() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("launcher")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_DIR / "launcher.log", encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def preflight(log: logging.Logger, wait_s: float) -> bool:
    """Retry the check while the rest of the plant is still waking up."""
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
            log.error("Pre-flight still failing after %.0fs: %s", wait_s, names)
            for check, detail in bad:
                log.error("    %s: %s", check, detail)
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
        code = subprocess.call(cmd, cwd=str(PROJECT))
    except Exception as exc:
        log.exception("Could not start VisionGuard: %s", exc)
        return -1
    log.info("VisionGuard exited with %d after %.0f minutes", code, (time.monotonic() - started) / 60)
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description="VisionGuard launcher / watchdog")
    ap.add_argument("--wait", type=float, default=180.0,
                    help="seconds to keep retrying pre-flight at boot (default 180)")
    ap.add_argument("--no-restart", action="store_true", help="do not supervise")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--check-only", action="store_true",
                    help="print the pre-flight report and exit, without starting anything")
    args, extra = ap.parse_known_args()

    if args.check_only:
        # The launcher is the console exe of the pair, so this is where a report can
        # actually be read; the windowed application has nowhere to print.
        from visionguard.diagnostics.preflight import main as preflight_main
        return preflight_main("config")

    log = setup_log()
    log.info("=" * 60)
    log.info("Launcher starting (%s)", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if not args.skip_preflight and not preflight(log, args.wait):
        log.error("Not starting. Fix the failures above, then run tools/preflight.py again.")
        return 1

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
