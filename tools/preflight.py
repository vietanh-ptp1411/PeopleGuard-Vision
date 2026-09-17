"""Run the pre-flight check from a source checkout.

    python tools/preflight.py [--json] [--quiet] [--config-dir DIR]

The check itself lives in visionguard/diagnostics/preflight.py so the built exe carries it
too - there it is reached with `VisionGuard.exe --check`.
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))

from visionguard.diagnostics import preflight

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="VisionGuard pre-flight check")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--config-dir", default="config")
    a = ap.parse_args()
    sys.exit(preflight.main(a.config_dir, a.json, a.quiet))
