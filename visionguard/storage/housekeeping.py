"""Keep the disk from filling up on a machine that is never turned off.

Everything this system produces grows without end unless something removes it: rows in the
event history, snapshot JPEGs, daily log files and - by far the largest - recorded clips.
A week of that is nothing; a year of it fills the drive and the software stops with no
warning that has anything to do with detection.

Two limits are applied, because either one alone lets you down:

    by age     the ordinary case - anything older than the retention window goes
    by size    the safety net - if recording has been unusually busy, the oldest clips go
               until the folder is back under its budget, whatever their age

Housekeeping runs once at start-up and then every few hours, on a worker thread, so a slow
delete of ten thousand files never touches the UI.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

log = logging.getLogger("SYSTEM")


def _older_than(root: Path, days: int, patterns: Tuple[str, ...]) -> int:
    if not days or days <= 0 or not root.exists():
        return 0
    cutoff = (datetime.now() - timedelta(days=int(days))).timestamp()
    removed = 0
    for pattern in patterns:
        for item in root.rglob(pattern):
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
                    removed += 1
            except OSError as exc:
                log.warning("Cannot delete %s: %s", item, exc)
    return removed


def _trim_to_budget(root: Path, max_gb: float, patterns: Tuple[str, ...]) -> Tuple[int, float]:
    """Delete the oldest files until the folder fits its budget. Returns (removed, GB left)."""
    if not max_gb or max_gb <= 0 or not root.exists():
        return 0, 0.0
    files: List[Tuple[float, int, Path]] = []
    total = 0
    for pattern in patterns:
        for item in root.rglob(pattern):
            try:
                if item.is_file():
                    st = item.stat()
                    files.append((st.st_mtime, st.st_size, item))
                    total += st.st_size
            except OSError:
                continue
    budget = int(max_gb * 1e9)
    if total <= budget:
        return 0, total / 1e9
    files.sort()                                   # oldest first
    removed = 0
    for _, size, item in files:
        if total <= budget:
            break
        try:
            item.unlink()
            total -= size
            removed += 1
        except OSError as exc:
            log.warning("Cannot delete %s: %s", item, exc)
    return removed, total / 1e9


def _drop_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for folder in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass


def run_once(app_config, events=None) -> Dict[str, int]:
    """Apply every retention rule. Safe to call from a worker thread."""
    keep = app_config.retention
    clip = app_config.clip
    report: Dict[str, int] = {}

    report["logs"] = _older_than(Path(keep.log_dir), keep.log_days, ("*.log",))
    report["snapshots"] = _older_than(Path(app_config.snapshot.directory), keep.snapshot_days,
                                      ("*.jpg", "*.jpeg", "*.png"))
    clips_root = Path(clip.directory)
    report["clips_old"] = _older_than(clips_root, clip.retention_days, ("*.mp4",))
    trimmed, left_gb = _trim_to_budget(clips_root, clip.max_total_gb, ("*.mp4",))
    report["clips_over_budget"] = trimmed
    report["clips_gb"] = round(left_gb, 2)
    _drop_empty_dirs(clips_root)

    if events is not None and keep.event_days > 0:
        try:
            report["event_rows"] = events.prune(keep.event_days, keep.event_max_rows)
        except Exception as exc:
            log.error("Pruning the event history failed: %s", exc)

    interesting = {k: v for k, v in report.items() if v}
    if interesting:
        log.info("Housekeeping: %s", ", ".join(f"{k}={v}" for k, v in interesting.items()))
    return report


def run_in_background(app_config, events=None) -> threading.Thread:
    """Deleting thousands of files must never block the UI thread."""
    thread = threading.Thread(target=run_once, args=(app_config, events),
                              name="housekeeping", daemon=True)
    thread.start()
    return thread
