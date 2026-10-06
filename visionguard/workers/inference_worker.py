"""InferenceWorker (QThread): YOLO inference + ROI/state-machine pipeline on the latest frame."""
from __future__ import annotations

import logging
import queue
import threading
import time
from copy import deepcopy
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
    model_unloaded = Signal()
    detection_state = Signal(bool, str)    # running, message
    inference_error = Signal(str)          # runtime error (system FAULT)

    MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self, detector: BaseDetector, buffer: LatestFrameBuffer, pipeline: ProcessingPipeline) -> None:
        super().__init__()
        self._detector = detector
        self._buffer = buffer
        self._pipeline = pipeline
        # One model serves every camera, taken in turn: a second YOLO instance would cost
        # another ~1 GB of VRAM to do the same work a few milliseconds later.
        self._sources: list = [(buffer, pipeline)]
        self._next = 0
        self._due: list = [0.0]
        self._max_fps = 0.0
        self._commands: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._running = True
        self._loading = False
        self._detecting = False
        self._errors = 0
        self._delivery_lock = threading.Lock()
        self._pending_results: dict[int, int] = {}
        self._load_lock = threading.Lock()
        self._requested_load = None
        self._load_queued = False
        self._load_version = 0
        self._generation_lock = threading.Lock()
        self._requested_generation = 0
        self._active_generation = 0

    # ------------------------------------------------------------------ API
    @property
    def detecting(self) -> bool:
        return self._detecting

    @property
    def requested_generation(self) -> int:
        with self._generation_lock:
            return self._requested_generation

    def is_model_loaded(self) -> bool:
        return self._detector.is_loaded()

    def request_load_model(self, config: DetectorConfig) -> None:
        with self._load_lock:
            if self._requested_load == config:
                return
            self._requested_load = deepcopy(config)
            self._load_version += 1
            if not self._load_queued:
                self._load_queued = True
                self._commands.put(("load_latest", None))

    def request_unload(self) -> None:
        with self._load_lock:
            self._requested_load = None
            self._load_version += 1
            self._commands.put(("unload", self._load_version))

    def result_delivered(self, camera: int, source_token: int) -> None:
        """At most one result (and its image) may be waiting on the UI per camera."""
        with self._delivery_lock:
            if self._pending_results.get(camera) == source_token:
                self._pending_results.pop(camera, None)

    def start_detection(self) -> None:
        with self._generation_lock:
            self._requested_generation += 1
            self._commands.put(("start", self._requested_generation))

    def stop_detection(self) -> None:
        with self._generation_lock:
            self._requested_generation += 1
            self._commands.put(("stop", self._requested_generation))

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
        try:
            while self._running:
                self._drain()
                if not self._running:
                    break
                if self._detecting and self._detector.is_loaded():
                    try:
                        self._step()
                    except Exception as exc:
                        log.exception("Inference pipeline failed")
                        self._detecting = False
                        self.inference_error.emit(str(exc))
                        self.detection_state.emit(False, "Inference pipeline failed")
                else:
                    self._wait_command(0.1)
        finally:
            self._detector.unload()
            for _, pipeline in self._sources:
                pipeline.close()
        log.debug("Inference worker stopped")

    def _wait_command(self, timeout: float) -> None:
        try:
            self._handle(self._commands.get(timeout=timeout))
        except queue.Empty:
            pass

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
            elif name == "load_latest":
                with self._load_lock:
                    config = self._requested_load
                    version = self._load_version
                if config is not None:
                    self._load(config)
                with self._load_lock:
                    self._load_queued = False
                    if self._requested_load is not None and self._load_version != version:
                        self._load_queued = True
                        self._commands.put(("load_latest", None))
                    else:
                        self._requested_load = None
            elif name == "unload":
                with self._load_lock:
                    if arg != self._load_version:
                        return  # A newer load request superseded this queued unload.
                self._detecting = False
                self._detector.unload()
                self.model_unloaded.emit()
                self.detection_state.emit(False, "Model unloaded")
            elif name == "sources":
                previous = self._sources
                self._sources = arg
                self._next = 0
                self._due = [0.0] * len(arg)
                for _, pipeline in previous:
                    if not any(pipeline is p for _, p in arg):
                        pipeline.close()
                with self._delivery_lock:
                    tokens = {p.camera: id(p) for _, p in arg}
                    self._pending_results = {i: t for i, t in self._pending_results.items()
                                             if tokens.get(i) == t}
            elif name == "start":
                if arg != self.requested_generation:
                    return  # Superseded before the inference thread reached START.
                if not self._detector.is_loaded():
                    self.detection_state.emit(False, "Model not loaded")
                    return
                for buffer, pipeline in self._sources:
                    buffer.clear()
                    pipeline.reset()
                self._errors = 0
                self._active_generation = arg
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

    def is_loading(self) -> bool:
        """True while the model is being read off disk.

        Loading yolo11n on CPU takes about twelve seconds and nothing can interrupt it -
        not the stop flag, not requestInterruption, because the thread is deep inside
        torch. Shutdown has to know, or it walks away from a thread that is still alive
        and Qt kills the process when that thread is destroyed.
        """
        return self._loading

    def _load(self, config: DetectorConfig) -> None:
        was_detecting = self._detecting
        self._detecting = False
        self._loading = True
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
        finally:
            self._loading = False
        self.model_loaded.emit(info)
        if was_detecting:
            self._detecting = True
            self.detection_state.emit(True, "Detection running")

    def set_sources(self, sources) -> None:
        """[(buffer, pipeline), ...] - one entry per camera being watched."""
        self._commands.put(("sources", list(sources) or [(self._buffer, self._pipeline)]))

    def set_max_fps(self, fps: float) -> None:
        """Inferences per second per camera. 0 lets it run flat out."""
        self._max_fps = max(0.0, float(fps or 0.0))

    def _step(self) -> None:
        # Round robin so a busy camera cannot starve the others, and each camera has its
        # own next-allowed time so the rate limit is per camera, not shared.
        count = len(self._sources)
        if len(self._due) != count:
            self._due = [0.0] * count
        interval = 1.0 / self._max_fps if self._max_fps > 0 else 0.0
        now = time.monotonic()
        timeout = 0.2 if count == 1 else 0.05
        frame = None
        pipeline = self._pipeline
        picked = -1
        for _ in range(count):
            if not self._running:
                return                      # a stop arrived; do not finish the sweep
            index = self._next
            self._next = (self._next + 1) % count
            if interval and now < self._due[index]:
                continue                    # this camera has had its turn recently
            buffer, pipeline = self._sources[index]
            with self._delivery_lock:
                if pipeline.camera in self._pending_results:
                    continue
            frame = buffer.get(timeout=timeout)
            if frame is not None:
                picked = index
                break
        if frame is None:
            # Includes waiting for delivery: do not busy-spin while the UI is occupied.
            self._wait_command(0.02)
            return
        if interval:
            self._due[picked] = max(now, self._due[picked]) + interval
        detection_generation = self._active_generation
        inference_started_at = time.monotonic()
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
        result = pipeline.process(frame, detections, inference_ms)
        result.inference_started_at = inference_started_at
        result.detection_generation = detection_generation
        with self._delivery_lock:
            self._pending_results[pipeline.camera] = result.source_token
        self.result_ready.emit(result)
