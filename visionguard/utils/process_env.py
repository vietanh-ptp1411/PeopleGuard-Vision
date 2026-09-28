"""Environment the process must be BORN with, and the relaunch that guarantees it.

The OpenCV 5 FFmpeg plugin takes its capture options from OPENCV_FFMPEG_CAPTURE_OPTIONS,
and it only sees the value the process inherited from its parent. Every way of setting it
from inside the process was tried and measured against a real camera: os.environ before
cv2 is imported, os.putenv, the C runtime's _putenv and _wputenv, kernel32's
SetEnvironmentVariableW, all of them together. The plugin opened the stream exactly as if
nothing had been set (1.6 s, the FFmpeg defaults) every time, while the same value put in
the shell before starting Python took effect (0.5 s). Other OPENCV_* variables do work
from inside the process; this one does not, and this module stops pretending otherwise.

So the value is set by whoever starts the process - the launcher, or the process itself
relaunching once - and a marker variable says it has been done.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

#: analyzeduration caps how long FFmpeg studies the stream before handing it over. By
#: default it reads about 27 frames of a 20 fps camera (1.35 s) to be sure of the frame
#: rate, and OpenCV opens cameras one at a time under a lock, so four cameras came up one
#: every 1.5 s. 0.2 s of stream is enough for it to still report 20 fps (0.05 s made it
#: say 40) and four cameras now stream within 1.8 s of START.
FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp|analyzeduration;200000"

MARK = "VISIONGUARD_ENV_READY"


def child_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment to start VisionGuard with. setdefault, so an operator's own
    OPENCV_FFMPEG_CAPTURE_OPTIONS in the system environment still wins."""
    env = dict(os.environ if base is None else base)
    env.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", FFMPEG_CAPTURE_OPTIONS)
    env.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    env[MARK] = "1"
    return env


def inherited() -> bool:
    return os.environ.get(MARK) == "1"


def relaunch(script: str) -> int:
    """Start this program again with child_env() and wait for it; returns its exit code.

    The first process stays alive only as a thin shell around the second, so whoever
    started it (a shortcut, the launcher, a terminal) still sees one process that ends when
    the window closes and carries its exit code.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *sys.argv[1:]]                      # the exe is the program
    else:
        cmd = [sys.executable, str(Path(script).resolve()), *sys.argv[1:]]
    try:
        return subprocess.call(cmd, env=child_env())
    except KeyboardInterrupt:
        return 130
