"""InferenceWorker (QThread): YOLO inference + ROI/state-machine pipeline on the latest frame."""
from __future__ import annotations

import logging
import queue
import time
from typing import Any, Tuple

from PySide6.QtCore import QThread, Signal

from ..config.schemas import DetectorConfig
from ..logic.pipeline import ProcessingPipeline
from ..vision.detector import BaseDetector, DetectorError
from .frame_buffer import LatestFrameBuffer

log = logging.getLogger("AI")


class InferenceWorker(QThread):
    result_ready = Signal(object)          # PipelineResult
    model_loaded = Signal(object)          # DetectorInfo
    model_load_failed = Signal(str)
    detection_state = Signal(bool, str)    # running, message
    inference_error = Signal(str)          # runtime error (system FAULT)

    MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self, detector: BaseDetector, buffer: LatestFrameBuffer, pipeline: ProcessingPipeline) -> None:
        super().__init__()
        self._detector = detector
        self._buffer = buffer
        self._pipeline = pipeline
        self._commands: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._running = True
        self._detecting = False
        self._errors = 0

    # ------------------------------------------------------------------ API
    @property
    def detecting(self) -> bool:
        return self._detecting

    def is_model_loaded(self) -> bool:
        return self._detector.is_loaded()

    def request_load_model(self, config: DetectorConfig) -> None:
        self._commands.put(("load", config))

    def request_unload(self) -> None:
        self._commands.put(("unload", None))

    def start_detection(self) -> None:
        self._commands.put(("start", None))

    def stop_detection(self) -> None:
        self._commands.put(("stop", None))

    def update_thresholds(self, confidence: float, iou: float) -> None:
        self._commands.put(("thresholds", (confidence, iou)))

    def reset_tracker(self) -> None:
        self._commands.put(("reset_tracker", None))

    def stop_worker(self) -> None:
        self._running = False
        self._commands.put(("quit", None))

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        log.debug("Inference worker started")
        while self._running:
            self._drain()
            if not self._running:
                break
            if self._detecting and self._detector.is_loaded():
                self._step()
            else:
                try:
                    self._handle(self._commands.get(timeout=0.05))
                except queue.Empty:
                    pass
        log.debug("Inference worker stopped")

    def _drain(self) -> None:
        for _ in range(8):
            try:
                self._handle(self._commands.get_nowait())
            except queue.Empty:
                return

    def _handle(self, cmd: Tuple[str, Any]) -> None:
        name, arg = cmd
        try:
            if name == "quit":
                self._running = False
            elif name == "load":
                self._load(arg)
            elif name == "unload":
                self._detecting = False
                self._detector.unload()
                self.detection_state.emit(False, "Model unloaded")
            elif name == "start":
                if not self._detector.is_loaded():
                    self.detection_state.emit(False, "Model not loaded")
                    return
                self._buffer.clear()
                self._pipeline.reset()
                self._errors = 0
                self._detecting = True
                self.detection_state.emit(True, "Detection running")
                log.info("Detection started")
            elif name == "stop":
                if self._detecting:
                    log.info("Detection stopped")
                self._detecting = False
                self.detection_state.emit(False, "Detection stopped")
            elif name == "thresholds":
                self._detector.update_thresholds(*arg)
            elif name == "reset_tracker":
                self._detector.reset_tracker()
        except Exception as exc:
            log.exception("Inference command %s failed: %s", name, exc)
            self.inference_error.emit(str(exc))

    def _load(self, config: DetectorConfig) -> None:
        was_detecting = self._detecting
        self._detecting = False
        try:
            info = self._detector.load(config)
        except DetectorError as exc:
            log.error("Model load failed: %s", exc)
            self.model_load_failed.emit(str(exc))
            return
        except Exception as exc:  # unexpected (CUDA driver etc.)
            log.exception("Model load crashed")
            self.model_load_failed.emit(f"Unexpected error: {exc}")
            return
        self.model_loaded.emit(info)
        if was_detecting:
            self._detecting = True
            self.detection_state.emit(True, "Detection running")

    def _step(self) -> None:
        frame = self._buffer.get(timeout=0.2)
        if frame is None:
            return
        t0 = time.perf_counter()
        try:
            detections = self._detector.detect(frame.image)
        except DetectorError as exc:
            self._errors += 1
            log.error("Inference error (%d): %s", self._errors, exc)
            if self._errors >= self.MAX_CONSECUTIVE_ERRORS:
                self._detecting = False
                self.detection_state.emit(False, "Detection stopped after repeated errors")
            self.inference_error.emit(str(exc))
            return
        self._errors = 0
        inference_ms = (time.perf_counter() - t0) * 1000.0
        result = self._pipeline.process(frame, detections, inference_ms)
        self.result_ready.emit(result)
