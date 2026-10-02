"""RoiManager: thread-safe collection of ROIs + JSON persistence (config/roi_config.json)."""
from __future__ import annotations

import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .roi_model import NormPoint, Roi, RoiType, new_roi_id

log = logging.getLogger("ROI")

Listener = Callable[[], None]


def _default_name(rtype: RoiType, rid: str) -> str:
    number = rid.split("_")[-1]
    if rtype == RoiType.INCLUDE:
        return rid.replace("_", " ")          # "ROI 001", as it has always been
    if rtype == RoiType.WARNING:
        return f"Warning {number}"
    return f"Exclusion {number}"


class RoiManager:
    """Owns the list of ROIs. UI edits it; the pipeline reads immutable snapshots."""

    def __init__(self, path: Path | str = "config/roi_config.json") -> None:
        self.path = Path(path)
        self._rois: List[Roi] = []
        self._lock = threading.RLock()
        self._listeners: List[Listener] = []
        self._dirty = False
        self._issued_ids: set[str] = set()

    # ------------------------------------------------------------------ listeners
    def add_listener(self, cb: Listener) -> None:
        self._listeners.append(cb)

    def remove_listener(self, cb: Listener) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        self._dirty = True
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as exc:  # listeners must never break the manager
                log.error("ROI listener failed: %s", exc)

    # ------------------------------------------------------------------ queries
    def all(self) -> List[Roi]:
        with self._lock:
            return deepcopy(self._rois)

    def snapshot(self) -> Tuple[Roi, ...]:
        """Immutable copy for the processing thread."""
        with self._lock:
            return tuple(deepcopy(self._rois))

    def get(self, roi_id: str) -> Optional[Roi]:
        with self._lock:
            for r in self._rois:
                if r.id == roi_id:
                    return deepcopy(r)
        return None

    def for_camera(self, camera: int) -> List[Roi]:
        """Only the zones drawn on one camera of the group."""
        return [r for r in self.all() if int(getattr(r, "camera", 0)) == int(camera)]

    def include_rois(self) -> List[Roi]:
        """Every watched zone, alarm and warning alike."""
        return [r for r in self.all() if r.is_include]

    def alarm_rois(self) -> List[Roi]:
        return [r for r in self.all() if r.is_alarm]

    def warning_rois(self) -> List[Roi]:
        return [r for r in self.all() if r.is_warning]

    def exclude_rois(self) -> List[Roi]:
        return [r for r in self.all() if r.is_exclude]

    def ids(self) -> List[str]:
        with self._lock:
            return [r.id for r in self._rois]

    def __len__(self) -> int:
        return len(self._rois)

    @property
    def dirty(self) -> bool:
        return self._dirty

    # ------------------------------------------------------------------ mutations
    def create(self, rtype: RoiType, points: Sequence[NormPoint], name: str = "", plc_device: str = "",
               camera: int = 0) -> Roi:
        with self._lock:
            rtype = RoiType.parse(rtype.value)
            rid = new_roi_id(rtype, list(self._issued_ids))
            self._issued_ids.add(rid)
            roi = Roi(
                id=rid,
                name=name or _default_name(rtype, rid),
                type=rtype,
                points=[(float(x), float(y)) for x, y in points],
                plc_device=plc_device.upper(),
                camera=int(camera),
            )
            roi.clamp()
            self._rois.append(roi)
        log.info("ROI created %s (%s, %d points)", roi.id, roi.type.value, len(roi.points))
        self._notify()
        return deepcopy(roi)

    def add(self, roi: Roi) -> None:
        with self._lock:
            if not roi.id.strip() or roi.id in self._issued_ids or roi.id == "FULL_FRAME" or roi.id.startswith("__"):
                roi.id = new_roi_id(roi.type, list(self._issued_ids))
            self._issued_ids.add(roi.id)
            roi.clamp()
            self._rois.append(deepcopy(roi))
        self._notify()

    def update(self, roi: Roi) -> bool:
        with self._lock:
            for i, r in enumerate(self._rois):
                if r.id == roi.id:
                    roi.clamp()
                    self._rois[i] = deepcopy(roi)
                    break
            else:
                return False
        self._notify()
        return True

    def update_points(self, roi_id: str, points: Sequence[NormPoint]) -> bool:
        with self._lock:
            for r in self._rois:
                if r.id == roi_id:
                    r.points = [(float(x), float(y)) for x, y in points]
                    r.clamp()
                    break
            else:
                return False
        self._notify()
        return True

    def set_enabled(self, roi_id: str, enabled: bool) -> bool:
        with self._lock:
            for r in self._rois:
                if r.id == roi_id:
                    r.enabled = bool(enabled)
                    break
            else:
                return False
        self._notify()
        return True

    def delete(self, roi_id: str) -> bool:
        with self._lock:
            before = len(self._rois)
            self._rois = [r for r in self._rois if r.id != roi_id]
            removed = len(self._rois) != before
        if removed:
            log.info("ROI deleted %s", roi_id)
            self._notify()
        return removed

    def clear(self) -> None:
        with self._lock:
            self._rois.clear()
        log.info("All ROIs cleared")
        self._notify()

    # ------------------------------------------------------------------ persistence
    def load(self) -> bool:
        if not self.path.exists():
            log.info("No ROI config at %s (starting empty)", self.path)
            return False
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            items = data.get("rois", []) if isinstance(data, dict) else data
            rois = [Roi.from_dict(d) for d in items if isinstance(d, dict)]
            issued = set(data.get("issued_ids", [])) if isinstance(data, dict) else set()
            issued.update(r.id for r in rois)
            seen = set()
            for roi in rois:
                if roi.id in seen or roi.id == "FULL_FRAME" or roi.id.startswith("__"):
                    roi.id = new_roi_id(roi.type, list(issued))
                    issued.add(roi.id)
                seen.add(roi.id)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.error("Cannot load ROI config %s: %s", self.path, exc)
            return False
        with self._lock:
            self._rois = rois
            self._issued_ids = issued
        self._dirty = False
        log.info("Loaded %d ROI(s) from %s", len(rois), self.path.name)
        self._notify()
        self._dirty = False
        return True

    def save(self) -> bool:
        with self._lock:
            payload = {"rois": [r.to_dict() for r in self._rois], "issued_ids": sorted(self._issued_ids)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Cannot save ROI config: %s", exc)
            return False
        self._dirty = False
        log.info("Saved %d ROI(s) to %s", len(payload["rois"]), self.path.name)
        return True
