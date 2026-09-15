"""Detector interface. The pipeline depends only on this, never on ultralytics directly."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import List

import numpy as np

from ..config.schemas import DetectorConfig
from .detection import Detection


class DetectorError(Exception):
    """Model load / inference failure (handled by the inference worker -> system FAULT)."""


@dataclass
class DetectorInfo:
    model_name: str = ""
    model_path: str = ""
    device: str = ""
    classes: List[int] = field(default_factory=list)
    tracking: bool = False
    backend: str = ""

    def summary(self) -> str:
        parts = [self.model_name or "no model", self.device]
        if self.tracking:
            parts.append("tracking")
        return " | ".join(p for p in parts if p)


class BaseDetector(abc.ABC):
    @abc.abstractmethod
    def load(self, config: DetectorConfig) -> DetectorInfo:
        """Load the model once. Raises DetectorError on failure."""

    @abc.abstractmethod
    def unload(self) -> None: ...

    @abc.abstractmethod
    def detect(self, image: np.ndarray) -> List[Detection]:
        """Run inference on one BGR frame. Raises DetectorError on failure."""

    @abc.abstractmethod
    def is_loaded(self) -> bool: ...

    @abc.abstractmethod
    def info(self) -> DetectorInfo: ...

    def update_thresholds(self, confidence: float, iou: float) -> None:
        """Hot-update thresholds without reloading (optional)."""

    def reset_tracker(self) -> None:
        """Forget track IDs (optional)."""
