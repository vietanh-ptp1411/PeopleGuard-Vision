"""ProcessingPipeline: detections -> ROI evaluation -> occupancy state machines.

Runs in the inference thread right after YOLO. Thread-safe with respect to ROI
edits (snapshots) and config updates.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from ..camera.base_camera import Frame
from ..config.schemas import LogicConfig
from ..roi.roi_manager import RoiManager
from ..roi.roi_model import Roi
from ..roi.roi_processor import RoiEvaluation, RoiProcessor
from ..utils.performance import FpsCounter, MovingAverage
from ..vision.detection import Detection
from .debounce import DebounceConfig
from .occupancy_state_machine import OccupancyState, OccupancyTracker, StateTransition


@dataclass
class PipelineResult:
    frame: Frame
    evaluation: RoiEvaluation
    transitions: List[StateTransition]
    area_state: OccupancyState
    area_occupied: bool
    area_progress: float
    roi_states: Dict[str, OccupancyState]
    rois: Tuple[Roi, ...]
    inference_ms: float
    pipeline_ms: float
    ai_fps: float
    timestamp: float = field(default_factory=time.time)

    @property
    def roi_occupied(self) -> Dict[str, bool]:
        return {rid: st.occupied for rid, st in self.roi_states.items()}


class ProcessingPipeline:
    def __init__(self, roi_manager: RoiManager, logic: LogicConfig) -> None:
        self._roi_manager = roi_manager
        self._lock = threading.RLock()
        self._rois: Tuple[Roi, ...] = roi_manager.snapshot()
        self._processor = RoiProcessor(logic.mode_enum, logic.intersection_threshold)
        self._tracker = OccupancyTracker(self._debounce(logic))
        self._fps = FpsCounter()
        self._infer_avg = MovingAverage(30)
        roi_manager.add_listener(self.refresh_rois)

    @staticmethod
    def _debounce(logic: LogicConfig) -> DebounceConfig:
        return DebounceConfig(logic.on_delay_ms, logic.off_delay_ms, logic.min_detection_frames)

    # ------------------------------------------------------------------ config
    def refresh_rois(self) -> None:
        with self._lock:
            self._rois = self._roi_manager.snapshot()

    def set_logic(self, logic: LogicConfig) -> None:
        with self._lock:
            self._processor.configure(logic.mode_enum, logic.intersection_threshold)
            self._tracker.set_config(self._debounce(logic))

    def reset(self) -> None:
        with self._lock:
            self._tracker.reset()
            self._fps.reset()
            self._infer_avg.reset()

    def rois(self) -> Tuple[Roi, ...]:
        with self._lock:
            return self._rois

    @property
    def area_occupied(self) -> bool:
        with self._lock:
            return self._tracker.area_occupied

    # ------------------------------------------------------------------ process
    def process(self, frame: Frame, detections: Sequence[Detection], inference_ms: float) -> PipelineResult:
        t0 = time.perf_counter()
        with self._lock:
            rois = self._rois
            evaluation = self._processor.evaluate(detections, frame.width, frame.height, rois)
            transitions = self._tracker.update(evaluation.roi_counts, time.monotonic())
            area_state = self._tracker.area_state
            area_occupied = self._tracker.area_occupied
            progress = self._tracker.area_progress()
            roi_states = self._tracker.roi_states()
            fps = self._fps.tick()
            self._infer_avg.add(inference_ms)
        return PipelineResult(
            frame=frame,
            evaluation=evaluation,
            transitions=transitions,
            area_state=area_state,
            area_occupied=area_occupied,
            area_progress=progress,
            roi_states=roi_states,
            rois=rois,
            inference_ms=self._infer_avg.value,
            pipeline_ms=(time.perf_counter() - t0) * 1000.0,
            ai_fps=fps,
        )
