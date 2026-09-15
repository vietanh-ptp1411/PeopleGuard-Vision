"""Load/save dataclass configs as JSON. Tolerant to missing/unknown keys."""
from __future__ import annotations

import dataclasses
import json
import logging
import typing
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Type, TypeVar

from .schemas import AIConfig, AppConfig, CameraConfig, PlcConfig

log = logging.getLogger("SYSTEM")
T = TypeVar("T")


# --------------------------------------------------------------------------- generic (de)serialization
def to_dict(obj: Any) -> Any:
    """dataclass -> plain JSON-serializable structure (Enums become their values)."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    return obj


def from_dict(cls: Type[T], data: Any) -> T:
    """Build a dataclass from a dict, filling defaults and ignoring unknown keys."""
    if data is None:
        return cls()
    if not is_dataclass(cls):
        return data  # type: ignore[return-value]
    if not isinstance(data, dict):
        log.warning("Config for %s is not an object, using defaults", cls.__name__)
        return cls()
    hints = typing.get_type_hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        ftype = hints.get(f.name, f.type)
        kwargs[f.name] = _coerce(ftype, value)
    try:
        return cls(**kwargs)
    except TypeError as exc:
        log.warning("Invalid config for %s (%s), using defaults", cls.__name__, exc)
        return cls()


def _coerce(ftype: Any, value: Any) -> Any:
    origin = typing.get_origin(ftype)
    if is_dataclass(ftype) and isinstance(value, dict):
        return from_dict(ftype, value)
    if isinstance(ftype, type) and issubclass(ftype, Enum):
        try:
            return ftype(value).value if issubclass(ftype, str) else ftype(value)
        except ValueError:
            return value
    if origin in (list, typing.List) and isinstance(value, list):
        args = typing.get_args(ftype)
        inner = args[0] if args else Any
        return [_coerce(inner, v) for v in value]
    if ftype is float and isinstance(value, (int, float)):
        return float(value)
    if ftype is int and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# --------------------------------------------------------------------------- settings bundle
@dataclass
class Settings:
    app: AppConfig = field(default_factory=AppConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    plc: PlcConfig = field(default_factory=PlcConfig)


class ConfigManager:
    """Owns the config directory and the JSON files inside it."""

    FILES: Dict[str, str] = {
        "app": "app_config.json",
        "camera": "camera_config.json",
        "ai": "ai_config.json",
        "plc": "plc_config.json",
    }
    TYPES: Dict[str, type] = {"app": AppConfig, "camera": CameraConfig, "ai": AIConfig, "plc": PlcConfig}

    def __init__(self, config_dir: Path | str = "config") -> None:
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings()

    # -- paths
    @property
    def roi_file(self) -> Path:
        return self.config_dir / "roi_config.json"

    @property
    def region_mapping_file(self) -> Path:
        """AI Camera mode: camera region id -> name + PLC device."""
        return self.config_dir / "region_mapping.json"

    def path_for(self, key: str) -> Path:
        return self.config_dir / self.FILES[key]

    # -- load
    def load_all(self) -> Settings:
        for key, cls in self.TYPES.items():
            setattr(self.settings, key, self._load_one(key, cls))
        return self.settings

    def _load_one(self, key: str, cls: Type[T]) -> T:
        path = self.path_for(key)
        if not path.exists():
            log.info("Config %s not found, creating defaults", path.name)
            obj = cls()
            self._write_json(path, to_dict(obj))
            return obj
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return from_dict(cls, data)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Cannot read %s (%s). Using defaults.", path, exc)
            return cls()

    # -- save
    def save_all(self) -> None:
        for key in self.TYPES:
            self.save(key)

    def save(self, key: str) -> bool:
        obj = getattr(self.settings, key)
        ok = self._write_json(self.path_for(key), to_dict(obj))
        if ok:
            log.debug("Saved %s", self.FILES[key])
        return ok

    @staticmethod
    def _write_json(path: Path, data: Any) -> bool:
        try:
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            tmp.replace(path)
            return True
        except OSError as exc:
            log.error("Cannot write %s: %s", path, exc)
            return False
