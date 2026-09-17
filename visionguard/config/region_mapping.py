"""Camera region -> name + PLC device mapping (config/region_mapping.json).

In AI Camera mode the detection zones live inside the camera. The PC only needs to know
which camera region id means what, and which PLC bit it drives:

    {"regions": [{"camera_region_id": "1", "name": "Robot Zone", "plc_device": "M100"}]}
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("EVENT")


@dataclass
class RegionMapping:
    camera_region_id: str
    name: str = ""
    plc_device: str = ""
    enabled: bool = True
    camera: int = 0        # which camera in the group reported this region

    @property
    def display_name(self) -> str:
        return self.name or f"Region {self.camera_region_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_region_id": str(self.camera_region_id),
            "name": self.name,
            "plc_device": self.plc_device,
            "enabled": bool(self.enabled),
            "camera": int(self.camera),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RegionMapping":
        return cls(
            camera_region_id=str(d.get("camera_region_id", "")).strip(),
            name=str(d.get("name", "") or ""),
            plc_device=str(d.get("plc_device", "") or "").upper(),
            enabled=bool(d.get("enabled", True)),
            camera=int(d.get("camera", 0) or 0),
        )


class RegionMappingManager:
    """Thread-safe list of region mappings with JSON persistence."""

    def __init__(self, path: Path | str = "config/region_mapping.json") -> None:
        self.path = Path(path)
        self._regions: List[RegionMapping] = []
        self._lock = threading.RLock()
        self._listeners: List[Callable[[], None]] = []

    # ------------------------------------------------------------------ listeners
    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as exc:
                log.error("region mapping listener failed: %s", exc)

    # ------------------------------------------------------------------ queries
    def all(self) -> List[RegionMapping]:
        with self._lock:
            return [RegionMapping(**r.__dict__) for r in self._regions]

    def enabled(self) -> List[RegionMapping]:
        return [r for r in self.all() if r.enabled]

    @staticmethod
    def key(region_id: str, camera: int = 0) -> str:
        """Zone key for the group.

        Camera 1 keeps the bare region id so existing mappings and configs stay valid;
        the others are prefixed, because two cameras may both report "Region 1".
        """
        return str(region_id) if int(camera) == 0 else f"C{int(camera) + 1}:{region_id}"

    def get(self, region_id: str, camera: int = 0) -> Optional[RegionMapping]:
        rid, cam = str(region_id), int(camera)
        with self._lock:
            for r in self._regions:
                if r.camera_region_id == rid and int(r.camera) == cam:
                    return RegionMapping(**r.__dict__)
        return None

    def device_for(self, region_id: str, camera: int = 0) -> str:
        r = self.get(region_id, camera)
        return r.plc_device if (r and r.enabled) else ""

    def name_for(self, region_id: str, camera: int = 0) -> str:
        r = self.get(region_id, camera)
        return r.display_name if r else f"Region {region_id}"

    def devices(self) -> Dict[str, str]:
        """zone key -> PLC device, for every enabled mapping that has one."""
        return {self.key(r.camera_region_id, r.camera): r.plc_device
                for r in self.all() if r.enabled and r.plc_device}

    def __len__(self) -> int:
        return len(self._regions)

    # ------------------------------------------------------------------ mutations
    def upsert(self, mapping: RegionMapping) -> None:
        with self._lock:
            for i, r in enumerate(self._regions):
                if r.camera_region_id == mapping.camera_region_id and int(r.camera) == int(mapping.camera):
                    self._regions[i] = mapping
                    break
            else:
                self._regions.append(mapping)
        self._notify()

    def delete(self, region_id: str, camera: int = 0) -> bool:
        rid, cam = str(region_id), int(camera)
        with self._lock:
            before = len(self._regions)
            self._regions = [r for r in self._regions
                             if not (r.camera_region_id == rid and int(r.camera) == cam)]
            removed = len(self._regions) != before
        if removed:
            self._notify()
        return removed

    def clear(self) -> None:
        with self._lock:
            self._regions.clear()
        self._notify()

    def ensure(self, region_id: str, camera: int = 0) -> RegionMapping:
        """Create a placeholder row the first time an unknown region id shows up."""
        existing = self.get(region_id, camera)
        if existing is not None:
            return existing
        mapping = RegionMapping(camera_region_id=str(region_id), name=f"Region {region_id}",
                                camera=int(camera))
        self.upsert(mapping)
        log.info("New camera region %s seen - add a PLC device for it in the AI Event tab", region_id)
        return mapping

    # ------------------------------------------------------------------ persistence
    def load(self) -> bool:
        if not self.path.exists():
            log.info("No region mapping at %s (starting empty)", self.path)
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("regions", []) if isinstance(data, dict) else data
            regions = [RegionMapping.from_dict(d) for d in items if isinstance(d, dict)]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.error("Cannot load %s: %s", self.path, exc)
            return False
        with self._lock:
            self._regions = [r for r in regions if r.camera_region_id]
        log.info("Loaded %d region mapping(s)", len(self._regions))
        self._notify()
        return True

    def save(self) -> bool:
        with self._lock:
            payload = {"regions": [r.to_dict() for r in self._regions]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            log.info("Saved %d region mapping(s)", len(payload["regions"]))
            return True
        except OSError as exc:
            log.error("Cannot save region mapping: %s", exc)
            return False
