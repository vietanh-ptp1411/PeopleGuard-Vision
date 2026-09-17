"""Where the application lives, whether it was started from source or from a built exe.

Frozen, `__file__` points inside the bundle's _internal folder, which is the wrong place
to look for config, models or logs - those sit beside the executable so an engineer can
open and edit them. One helper, used by everything that needs a path.
"""
from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The folder that holds config/, models/, logs/ and events/."""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def resource_dir() -> Path:
    """Read-only files shipped inside the bundle (source tree: the same folder)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()
