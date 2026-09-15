"""SystemController: the only object that talks to workers, config, ROI manager and storage.

Lives in the UI thread. Workers push signals here; the controller derives the system
status (RUNNING / FAULT / STOPPED), the area status (CLEAR / OCCUPIED / FAULT) and
the PLC output state, and republishes compact signals for the widgets.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ...camera.base_camera import Frame
from ...camera.camera_manager import CameraManager
from ...camera.events.camera_event import CameraEvent, CameraEventType
from ...camera.events.event_manager import EventManager
from ...camera.rtsp_camera import RtspCamera
from ...camera.video.rtsp_receiver import RtspVideoReceiver
from ...config.config_manager import ConfigManager
from ...config.region_mapping import RegionMapping, RegionMappingManager
from ...config.schemas import (AIConfig, AppConfig, CameraConfig, CameraType, DetectionMode,
                               EventProviderType, PlcConfig)
from ...logic.camera_event_state_machine import CameraEventStateMachine, ZoneState
from ...logic.occupancy_state_machine import AreaStatus, OccupancyTracker
from ...logic.pipeline import PipelineResult, ProcessingPipeline
from ...plc.plc_manager import WORD_AI_ERROR, WORD_CAMERA_ERROR, PlcOutputState
from ...roi.roi_manager import RoiManager
from ...roi.roi_model import RoiType
from ...storage.event_repository import EventRepository, EventType
from ...storage.snapshot_saver import SnapshotSaver
from ...vision.yolo_detector import YoloDetector
from ...workers.camera_event_worker import CameraEventWorker, EventChannelState
from ...workers.camera_worker import CameraState, CameraWorker
from ...workers.frame_buffer import LatestFrameBuffer
from ...workers.inference_worker import InferenceWorker
from ...workers.plc_worker import PlcWorker

log = logging.getLogger("SYSTEM")

RESULT_TIMEOUT_S = 3.0   # no AI result for this long while RUNNING -> FAULT
STARTUP_GRACE_S = 10.0   # after START: camera/AI may still be coming up -> STARTING, not FAULT
EVENT_TICK_MS = 100      # AI Camera mode: how often the event debounce timers are advanced
EVENT_HISTORY = 500      # camera events kept in memory for the monitor panel


class SystemController(QObject):
    # camera
    camera_state = Signal(str, str)
    camera_fps = Signal(float)
    camera_info = Signal(object)
    frame_ready = Signal(object)
    devices_scanned = Signal(str, list)
    video_position = Signal(int, int)
    # AI (PC / YOLO)
    result_ready = Signal(object)
    model_status = Signal(bool, str)
    detection_state = Signal(bool, str)
    # AI camera events
    camera_event = Signal(object)          # CameraEvent, for the event monitor
    raw_camera_event = Signal(str)         # payload exactly as the camera sent it
    event_channel_state = Signal(str, str) # EventChannelState, message
    zone_states_changed = Signal(object)   # {region id: ZoneState}
    detection_mode_changed = Signal(str)   # DetectionMode value
    regions_changed = Signal()
    # PLC
    plc_state = Signal(bool, str)
    plc_latency = Signal(float)
    plc_memory = Signal(object)
    heartbeat = Signal(bool)
    io_result = Signal(bool, str)
    # system
    area_status = Signal(str)           # CLEAR / OCCUPIED / FAULT / STOPPED / STARTING
    system_state = Signal(str, str)     # RUNNING / FAULT / STOPPED, message
    message = Signal(str)
    event_logged = Signal(object)
    rois_changed = Signal()
    dropped_frames = Signal(int)

    def __init__(self, config_manager: ConfigManager, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.cm = config_manager
        self.settings = self.cm.load_all()

        self.roi_manager = RoiManager(self.cm.roi_file)
        self.roi_manager.load()
        self.roi_manager.add_listener(self._on_rois_changed)

        self.region_mapping = RegionMappingManager(self.cm.region_mapping_file)
        self.region_mapping.load()
        self.region_mapping.add_listener(self._on_regions_changed)

        self.camera_manager = CameraManager()
        self.event_manager = EventManager()
        self.event_state = CameraEventStateMachine(self.settings.camera.ai_camera.logic)
        self.buffer = LatestFrameBuffer()
        self.detector = YoloDetector()
        self.pipeline = ProcessingPipeline(self.roi_manager, self.settings.ai.logic)
        self.events = EventRepository(self.settings.app.events_db_path)
        self.snapshots = SnapshotSaver(self.settings.app.snapshot)

        # workers
        self.camera_worker = CameraWorker(self.camera_manager, self.buffer, self.settings.camera)
        self.inference_worker = InferenceWorker(self.detector, self.buffer, self.pipeline)
        self.event_worker = CameraEventWorker(self.event_manager, self.settings.camera.ai_camera,
                                              self.settings.camera.brand_enum)
        self.plc_worker = PlcWorker(self.settings.plc)
        self._wire_workers()

        # state
        self._system_running = False
        self._camera_state = CameraState.DISCONNECTED
        self._model_loaded = False
        self._detecting = False
        self._ai_error = ""
        self._plc_connected = False
        self._plc_message = ""
        self._last_result: Optional[PipelineResult] = None
        self._last_result_time = 0.0
        self._start_time = 0.0
        self._last_output: Optional[PlcOutputState] = None
        self._area = AreaStatus.STOPPED.value
        self._system = "STOPPED"
        self._fault_reason = ""
        self._in_fault = False
        self._system_msg = ""
        self._start_detection_when_loaded = False
        # AI camera mode
        self._event_channel = EventChannelState.DISCONNECTED
        self._event_message = ""
        self._last_camera_event: Optional[CameraEvent] = None
        self._event_history: List[CameraEvent] = []
        self._last_frame: Optional[Frame] = None
        self._zone_states: Dict[str, ZoneState] = {}
        self._area_was_occupied = False

        self._watchdog = QTimer(self)
        self._watchdog.setInterval(500)
        self._watchdog.timeout.connect(self._tick)
        self._watchdog.start()

        self._event_timer = QTimer(self)
        self._event_timer.setInterval(EVENT_TICK_MS)
        self._event_timer.timeout.connect(self._tick_events)

        for w in (self.camera_worker, self.inference_worker, self.event_worker, self.plc_worker):
            w.start()

        if self.ai_camera_mode:
            log.info("Detection mode: AI Camera (%s) - YOLO stays available but idle",
                     self.settings.camera.brand_enum.label)
            self._event_timer.start()
            self.event_worker.request_connect()
        else:
            log.info("Detection mode: PC AI / YOLO")
            self.event_worker.request_disable()
            if self.settings.ai.detector.auto_load_on_start:
                self.load_model()
        if self.settings.plc.simulation_mode:
            self.plc_connect()   # harmless: virtual PLC

    # ================================================================== wiring
    def _wire_workers(self) -> None:
        cw = self.camera_worker
        cw.frame_ready.connect(self._on_frame)
        cw.state_changed.connect(self._on_camera_state)
        cw.info_changed.connect(self.camera_info)
        cw.fps_changed.connect(self.camera_fps)
        cw.video_position.connect(self.video_position)
        iw = self.inference_worker
        iw.result_ready.connect(self._on_result)
        iw.model_loaded.connect(self._on_model_loaded)
        iw.model_load_failed.connect(self._on_model_failed)
        iw.detection_state.connect(self._on_detection_state)
        iw.inference_error.connect(self._on_inference_error)
        ew = self.event_worker
        ew.event_received.connect(self._on_camera_event)
        ew.state_changed.connect(self._on_event_channel_state)
        ew.raw_event.connect(self.raw_camera_event)
        ew.test_result.connect(self._on_event_test_result)
        ew.provider_info.connect(lambda text: self.message.emit(f"Event provider: {text}"))
        pw = self.plc_worker
        pw.connection_changed.connect(self._on_plc_connection)
        pw.latency_changed.connect(self.plc_latency)
        pw.memory_changed.connect(self.plc_memory)
        pw.heartbeat_toggled.connect(self.heartbeat)
        pw.io_result.connect(self.io_result)
        pw.plc_error.connect(lambda msg: self.message.emit(f"PLC: {msg}"))

    # ================================================================== camera slots
    def apply_camera_config(self, cfg: CameraConfig) -> None:
        previous_mode = self.settings.camera.mode_enum
        self.settings.camera = cfg
        self.cm.save("camera")
        self.camera_worker.set_config(cfg)
        self.event_state.set_config(cfg.ai_camera.logic)
        if cfg.is_ai_camera:
            self.event_worker.set_config(cfg.ai_camera, cfg.brand_enum)
            if not self._event_timer.isActive():
                self._event_timer.start()
        else:
            self._event_timer.stop()
            self.event_worker.request_disable()
        if cfg.mode_enum != previous_mode:
            self.detection_mode_changed.emit(cfg.detection_mode)
        self.message.emit("Camera configuration saved")

    def camera_connect(self) -> None:
        self.camera_worker.request_connect()

    def camera_disconnect(self) -> None:
        self.camera_worker.request_disconnect()

    def camera_start(self) -> None:
        self.camera_worker.request_start()

    def camera_stop(self) -> None:
        self.camera_worker.request_stop()

    def camera_test(self) -> None:
        self.camera_worker.request_test()

    def video_command(self, name: str, arg=None) -> None:
        self.camera_worker.video_command(name, arg)

    def scan_devices(self, camera_type: str) -> None:
        def _scan() -> None:
            try:
                ct = CameraType(camera_type)
            except ValueError:
                return
            devices = self.camera_manager.scan_devices(ct, self.settings.camera.industrial.cti_path)
            self.devices_scanned.emit(camera_type, devices)

        self.message.emit(f"Scanning {camera_type} devices...")
        threading.Thread(target=_scan, name="scan", daemon=True).start()

    def rtsp_test(self) -> None:
        cfg = self.settings.camera.rtsp

        def _test() -> None:
            ok, msg = RtspCamera.test_connection(cfg)
            self.camera_state.emit("TEST_OK" if ok else "TEST_FAIL", f"RTSP test: {msg}")

        self.message.emit("Testing RTSP connection...")
        threading.Thread(target=_test, name="rtsp-test", daemon=True).start()

    # ================================================================== detection mode
    @property
    def ai_camera_mode(self) -> bool:
        return self.settings.camera.is_ai_camera

    @property
    def detection_mode(self) -> DetectionMode:
        return self.settings.camera.mode_enum

    def set_detection_mode(self, mode: DetectionMode) -> None:
        """Switch between the camera's own AI and the PC YOLO pipeline."""
        if mode == self.detection_mode:
            return
        was_running = self._system_running
        if was_running:
            self.stop_system()
        self.settings.camera.detection_mode = mode.value
        self.cm.save("camera")
        self.camera_worker.set_config(self.settings.camera)
        if mode == DetectionMode.AI_CAMERA:
            self.inference_worker.stop_detection()
            self.event_state.reset()
            self.event_worker.set_config(self.settings.camera.ai_camera, self.settings.camera.brand_enum)
            self.event_worker.request_connect()
            self._event_timer.start()
        else:
            self._event_timer.stop()
            self.event_worker.request_disable()
        log.info("Detection mode changed to %s", mode.label)
        self.message.emit(f"Detection mode: {mode.label}")
        self.detection_mode_changed.emit(mode.value)
        self.camera_disconnect()
        self._recompute()
        if was_running:
            self.start_system()

    # ================================================================== AI camera event channel
    def event_channel_connect(self) -> None:
        self.event_worker.request_connect()

    def event_channel_disconnect(self) -> None:
        self.event_worker.request_disconnect()

    def test_ai_camera(self) -> None:
        self.event_worker.request_test_camera()

    def test_event_channel(self, seconds: float = 6.0) -> None:
        self.message.emit(f"Listening to the camera event channel for {seconds:.0f}s...")
        self.event_worker.request_test_event(seconds)

    def simulate_event(self, action: str, region_id: str = "1") -> None:
        self.event_worker.simulate(action, region_id)

    def camera_events(self, limit: int = EVENT_HISTORY) -> List[CameraEvent]:
        return self._event_history[-limit:]

    @property
    def last_camera_event(self) -> Optional[CameraEvent]:
        return self._last_camera_event

    @property
    def event_channel_online(self) -> bool:
        return self._event_channel == EventChannelState.ONLINE

    def zone_states(self) -> Dict[str, ZoneState]:
        return dict(self._zone_states)

    # ================================================================== region mapping
    def save_regions(self) -> bool:
        ok = self.region_mapping.save()
        self.message.emit("Region mapping saved" if ok else "Region mapping save FAILED (see log)")
        return ok

    def update_region(self, region_id: str, name: str, plc_device: str, enabled: bool) -> None:
        self.region_mapping.upsert(RegionMapping(camera_region_id=str(region_id), name=name,
                                                 plc_device=plc_device.upper(), enabled=enabled))
        self.message.emit(f"Region {region_id} updated")

    def delete_region(self, region_id: str) -> None:
        if self.region_mapping.delete(region_id):
            self.event_state.forget_zone(region_id)
            self.message.emit(f"Region {region_id} removed")

    def _on_regions_changed(self) -> None:
        self.regions_changed.emit()
        self._recompute()

    # ================================================================== AI slots
    def apply_ai_config(self, cfg: AIConfig) -> None:
        old = self.settings.ai.detector
        self.settings.ai = cfg
        self.cm.save("ai")
        self.pipeline.set_logic(cfg.logic)
        new = cfg.detector
        if self._model_loaded:
            reload_needed = (old.model_path, old.device, old.imgsz, old.tracking_enabled, old.half) != (
                new.model_path, new.device, new.imgsz, new.tracking_enabled, new.half)
            if reload_needed:
                self.message.emit("AI settings saved - press 'Load Model' to apply model/device changes")
            else:
                self.inference_worker.update_thresholds(new.confidence, new.iou)
                self.message.emit("AI settings applied")
        else:
            self.message.emit("AI settings saved")

    def load_model(self) -> None:
        self.model_status.emit(False, "Loading model...")
        self.inference_worker.request_load_model(self.settings.ai.detector)

    def start_detection(self) -> None:
        if not self._model_loaded:
            self._start_detection_when_loaded = True
            self.load_model()
            return
        self.inference_worker.start_detection()

    def stop_detection(self) -> None:
        self._start_detection_when_loaded = False
        self.inference_worker.stop_detection()

    # ================================================================== PLC slots
    def apply_plc_config(self, cfg: PlcConfig) -> None:
        self.settings.plc = cfg
        self.cm.save("plc")
        self.plc_worker.reconfigure(cfg)
        self.message.emit("PLC configuration saved" + (" (simulation)" if cfg.simulation_mode else ""))

    def set_simulation(self, on: bool) -> None:
        if self.settings.plc.simulation_mode == on:
            return
        self.settings.plc.simulation_mode = on
        self.cm.save("plc")
        self.plc_worker.reconfigure(self.settings.plc)
        if on:
            self.plc_worker.request_connect()
        log.info("PLC simulation mode %s", "ON" if on else "OFF")

    def plc_connect(self) -> None:
        self.plc_worker.request_connect()

    def plc_disconnect(self) -> None:
        self.plc_worker.request_disconnect()

    def plc_test(self) -> None:
        self.plc_worker.request_test()

    def plc_manual_write(self, device: str, value: int) -> None:
        self.plc_worker.manual_write(device, value)

    def plc_manual_read(self, device: str) -> None:
        self.plc_worker.manual_read(device)

    # ================================================================== ROI slots
    def add_roi(self, type_value: str, points: List) -> None:
        rtype = RoiType(type_value)
        roi = self.roi_manager.create(rtype, points)
        self.message.emit(f"{rtype.label} ROI {roi.id} created ({len(points)} points) - remember to Save ROI")

    def update_roi_points(self, roi_id: str, points: List) -> None:
        self.roi_manager.update_points(roi_id, points)

    def update_roi_fields(self, roi_id: str, name: str, plc_device: str, enabled: bool) -> None:
        roi = self.roi_manager.get(roi_id)
        if roi is None:
            return
        roi.name, roi.plc_device, roi.enabled = name, plc_device.upper(), enabled
        self.roi_manager.update(roi)
        self.message.emit(f"ROI {roi_id} updated")

    def delete_roi(self, roi_id: str) -> None:
        if self.roi_manager.delete(roi_id):
            self.message.emit(f"ROI {roi_id} deleted")

    def clear_rois(self) -> None:
        self.roi_manager.clear()

    def save_rois(self) -> bool:
        ok = self.roi_manager.save()
        self.message.emit("ROI configuration saved" if ok else "ROI save FAILED (see log)")
        return ok

    def _on_rois_changed(self) -> None:
        self.rois_changed.emit()
        self._recompute()

    # ================================================================== app config
    def apply_app_config(self, cfg: AppConfig) -> None:
        self.settings.app = cfg
        self.cm.save("app")
        self.snapshots.set_config(cfg.snapshot)

    # ================================================================== system
    @property
    def running(self) -> bool:
        return self._system_running

    def start_system(self) -> None:
        if self._system_running:
            return
        ai_mode = self.ai_camera_mode
        if not ai_mode and not self.roi_manager.include_rois():
            self.message.emit("Warning: no INCLUDE ROI defined - the area will never be OCCUPIED")
        self._system_running = True
        self._start_time = time.monotonic()
        self._ai_error = ""
        log.info("SYSTEM START requested (%s)", self.detection_mode.label)
        self._log_event(EventType.SYSTEM_START,
                        details=f"{self.detection_mode.label} / "
                                f"{'Simulation' if self.settings.plc.simulation_mode else 'Real PLC'}")
        if self._camera_state != CameraState.STREAMING:
            self.camera_worker.request_start()
        if not self._plc_connected:
            self.plc_worker.request_connect()

        if ai_mode:
            # The camera is the detector: no model, no inference. Zone states are deliberately
            # NOT reset - a person already reported inside stays inside.
            if not self._event_timer.isActive():
                self._event_timer.start()
            if self._event_channel not in (EventChannelState.ONLINE, EventChannelState.CONNECTING):
                self.event_worker.request_connect()
        else:
            self._last_result = None
            self._last_result_time = 0.0
            self.pipeline.reset()
            if self._model_loaded:
                self.inference_worker.start_detection()
            else:
                self._start_detection_when_loaded = True
                self.load_model()
        self._recompute()

    def stop_system(self) -> None:
        if not self._system_running:
            return
        self._system_running = False
        self._start_detection_when_loaded = False
        if not self.ai_camera_mode:
            self.inference_worker.stop_detection()
        # In AI Camera mode the video and the event channel stay up so the operator keeps
        # seeing the picture and the monitor; only the PLC output is released.
        log.info("SYSTEM STOP")
        self._log_event(EventType.SYSTEM_STOP)
        self._recompute(force_plc=True)

    def shutdown(self) -> None:
        log.info("Shutting down")
        self._watchdog.stop()
        try:
            if self._system_running:
                self._system_running = False
                self._recompute(force_plc=True)
                time.sleep(0.15)  # let the PLC worker push the STOPPED state
        except Exception:
            pass
        self._event_timer.stop()
        workers = (self.inference_worker, self.camera_worker, self.event_worker, self.plc_worker)
        for w in workers:
            w.stop_worker()
        for w in workers:
            if not w.wait(3000):
                log.warning("%s did not stop in time", type(w).__name__)
        self.snapshots.shutdown()
        self.events.close()

    # ================================================================== worker callbacks
    def _on_frame(self, frame: Frame) -> None:
        self._last_frame = frame          # newest picture, used for AI camera event snapshots
        self.frame_ready.emit(frame)

    def _on_camera_state(self, state: str, msg: str) -> None:
        if state in ("TEST_OK", "TEST_FAIL"):
            self.camera_state.emit(state, msg)
            self.message.emit(msg)
            return
        prev = self._camera_state
        self._camera_state = state
        self.camera_state.emit(state, msg)
        if state == CameraState.STREAMING and prev != CameraState.STREAMING:
            self._log_event(EventType.CAMERA, details="Camera streaming")
        if state in (CameraState.LOST, CameraState.RECONNECTING, CameraState.ERROR) and prev == CameraState.STREAMING:
            self._log_event(EventType.CAMERA, details=f"Camera lost: {msg}")
            self.buffer.clear()
        self._recompute()

    def _on_result(self, result: PipelineResult) -> None:
        self._last_result = result
        self._last_result_time = time.monotonic()
        if self._ai_error:
            self._ai_error = ""
        roi_names = {r.id: r.name for r in result.rois}
        for t in result.transitions:
            if t.roi_id == OccupancyTracker.GLOBAL_ID:
                if t.became_occupied:
                    log.info("AREA OCCUPIED")
                    self._log_event(EventType.AREA_OCCUPIED, details=f"{result.evaluation.in_roi} person(s) in ROI")
                elif t.became_clear:
                    log.info("AREA CLEAR")
                    self._log_event(EventType.AREA_CLEAR)
                continue
            name = roi_names.get(t.roi_id, t.roi_id)
            if t.became_occupied:
                logging.getLogger("ROI").info("Person entered %s (%s)", name, t.roi_id)
                snap = self.snapshots.save(result.frame.image, t.roi_id, "PERSON") if self._system_running else None
                self._log_event(EventType.PERSON_ENTERED, t.roi_id, name, snapshot_path=str(snap) if snap else "")
            elif t.became_clear:
                logging.getLogger("ROI").info("Person left %s (%s)", name, t.roi_id)
                self._log_event(EventType.PERSON_LEFT, t.roi_id, name)
        self.result_ready.emit(result)
        self._recompute()

    def _on_model_loaded(self, info) -> None:
        self._model_loaded = True
        self.model_status.emit(True, f"Model loaded: {info.summary()}")
        self.message.emit(f"YOLO loaded: {info.summary()}")
        if self._start_detection_when_loaded:
            self._start_detection_when_loaded = False
            self.inference_worker.start_detection()
        self._recompute()

    def _on_model_failed(self, msg: str) -> None:
        self._model_loaded = False
        self._start_detection_when_loaded = False
        self._ai_error = msg
        self.model_status.emit(False, f"Model load failed: {msg}")
        self.message.emit(f"Model load failed: {msg}")
        self._log_event(EventType.AI, details=f"Model load failed: {msg}")
        self._recompute()

    def _on_detection_state(self, running: bool, msg: str) -> None:
        self._detecting = running
        self.detection_state.emit(running, msg)
        self._recompute()

    def _on_inference_error(self, msg: str) -> None:
        self._ai_error = msg
        self._log_event(EventType.AI, details=f"Inference error: {msg}")
        self._recompute()

    def _on_camera_event(self, event: CameraEvent) -> None:
        """An alarm arrived from the camera (AI Camera mode)."""
        self._event_history.append(event)
        if len(self._event_history) > EVENT_HISTORY:
            del self._event_history[: len(self._event_history) - EVENT_HISTORY]
        self._last_camera_event = event
        self.camera_event.emit(event)

        etype = event.type_enum
        if etype.is_health:
            self._log_event(EventType.CAMERA, details=event.summary())
            return
        if event.region_id:
            self.region_mapping.ensure(event.region_id)
        if event.is_non_human_target:
            log.info("Camera event ignored (%s is not a person): %s", event.target_enum.value, event.summary())
            return
        self.event_state.handle_event(event)
        self._tick_events()

    def _on_event_channel_state(self, state: str, msg: str) -> None:
        previous = self._event_channel
        self._event_channel = state
        self._event_message = msg
        self.event_channel_state.emit(state, msg)
        if state == EventChannelState.ONLINE and previous != EventChannelState.ONLINE:
            log.info("AI event channel ONLINE: %s", msg)
            self._log_event(EventType.CAMERA, details=f"Event channel online: {msg}")
        elif previous == EventChannelState.ONLINE and state != EventChannelState.ONLINE:
            self._log_event(EventType.CAMERA, details=f"Event channel lost: {msg}")
        self._recompute()

    def _on_event_test_result(self, ok: bool, message: str) -> None:
        self.camera_state.emit("TEST_OK" if ok else "TEST_FAIL", message)
        self.message.emit(message)

    def _tick_events(self) -> None:
        """Advance the event debounce timers and publish whatever changed."""
        if not self.ai_camera_mode:
            return
        transitions = self.event_state.tick()
        states = self.event_state.zone_states()
        if states != self._zone_states:
            self._zone_states = states
            self.zone_states_changed.emit(dict(states))
        for tr in transitions:
            name = self.region_mapping.name_for(tr.region_id)
            if tr.occupied:
                logging.getLogger("ROI").info("Person entered %s (region %s)", name, tr.region_id)
                snap = (self.snapshots.save(self._last_frame.image, f"REGION_{tr.region_id}", "PERSON")
                        if (self._system_running and self._last_frame is not None) else None)
                self._log_event(EventType.PERSON_ENTERED, tr.region_id, name,
                                snapshot_path=str(snap) if snap else "")
            else:
                logging.getLogger("ROI").info("Zone clear: %s (region %s)", name, tr.region_id)
                self._log_event(EventType.PERSON_LEFT, tr.region_id, name)
        occupied = self.event_state.area_occupied
        if occupied != self._area_was_occupied:
            self._area_was_occupied = occupied
            if occupied:
                log.info("AREA OCCUPIED (camera AI)")
                self._log_event(EventType.AREA_OCCUPIED,
                                details=", ".join(self.event_state.occupied_zone_ids()) or "camera event")
            else:
                log.info("AREA CLEAR (camera AI)")
                self._log_event(EventType.AREA_CLEAR)
        self._recompute()

    def _on_plc_connection(self, connected: bool, msg: str) -> None:
        prev = self._plc_connected
        self._plc_connected = connected
        self._plc_message = msg
        self.plc_state.emit(connected, msg)
        if connected != prev:
            self._log_event(EventType.PLC, details=("Connected: " if connected else "Disconnected: ") + msg)
            if connected and self._last_output is not None:
                self.plc_worker.update_output(self._last_output)
        self._recompute()

    # ================================================================== status derivation
    def _tick(self) -> None:
        """Watchdog: detect a silent AI pipeline and emit drop statistics."""
        if self.ai_camera_mode:
            self.dropped_frames.emit(self.buffer.dropped)
            return
        if self._system_running and self._camera_state == CameraState.STREAMING and not self._ai_error:
            now = time.monotonic()
            if self._last_result is not None and now - self._last_result_time > RESULT_TIMEOUT_S:
                self._ai_error = f"No AI result for {RESULT_TIMEOUT_S:.0f}s"
                self._recompute()
            elif self._last_result is None and now - self._start_time > STARTUP_GRACE_S:
                self._ai_error = f"No AI result {STARTUP_GRACE_S:.0f}s after start"
                self._recompute()
        elif self._system_running and self._last_result is None and time.monotonic() - self._start_time > STARTUP_GRACE_S:
            self._recompute()  # grace period over: STARTING -> FAULT if camera/AI still not up
        self.dropped_frames.emit(self.buffer.dropped)

    def _recompute(self, force_plc: bool = False) -> None:
        running = self._system_running
        ai_mode = self.ai_camera_mode
        logic = self.settings.camera.ai_camera.logic
        video_ok = self._camera_state == CameraState.STREAMING

        if ai_mode:
            # The camera detects; the event channel is what must be alive. Once it is online,
            # silence means "nobody in the zone", so there is always valid information.
            detector_ok = self._event_channel == EventChannelState.ONLINE
            has_data = detector_ok
            detector_reason = self._event_message or "AI event channel offline"
            # With simulated events there is no camera at all, so a missing picture is not a fault.
            simulated = self.settings.camera.ai_camera.provider_enum == EventProviderType.MOCK
            video_matters = bool(logic.fault_on_video_loss) and not simulated
            detector_matters = bool(logic.fault_on_event_loss)
            area_occupied = self.event_state.area_occupied
        else:
            detector_ok = self._model_loaded and self._detecting and not self._ai_error
            has_data = self._last_result is not None
            detector_reason = self._ai_error or "AI not running"
            video_matters = True
            detector_matters = True
            area_occupied = bool(self._last_result.area_occupied) if self._last_result else False
        camera_ok = video_ok

        fault, code, reason = False, 0, ""
        starting = False
        if running:
            # Right after START the links may still be coming up: report STARTING (not FAULT)
            # unless a hard error is already known or the grace period is over.
            in_grace = (not has_data) and (time.monotonic() - self._start_time) < STARTUP_GRACE_S
            hard_error = self._camera_state in (CameraState.LOST, CameraState.ERROR, CameraState.FINISHED)
            hard_error = hard_error or (self._event_channel == EventChannelState.ERROR if ai_mode
                                        else bool(self._ai_error))
            if video_matters and not video_ok:
                if in_grace and not hard_error:
                    starting = True
                else:
                    fault, code, reason = True, WORD_CAMERA_ERROR, f"Video {self._camera_state}"
            elif detector_matters and not detector_ok:
                if in_grace and not hard_error:
                    starting = True
                else:
                    fault, code, reason = True, WORD_AI_ERROR, detector_reason
            elif not has_data:
                starting = True

        if not running:
            area = AreaStatus.STOPPED.value
        elif fault:
            area = AreaStatus.FAULT.value
        elif starting:
            area = "STARTING"
        else:
            area = AreaStatus.OCCUPIED.value if area_occupied else AreaStatus.CLEAR.value

        if not running:
            system, msg = "STOPPED", ""
        elif fault:
            system, msg = "FAULT", reason
        elif not self._plc_connected:
            system, msg = "FAULT", f"PLC disconnected ({self._plc_message})" if self._plc_message else "PLC disconnected"
        elif starting:
            detail = ("event channel " + self._event_channel.lower()) if ai_mode else \
                     ("AI " + ("running" if detector_ok else "starting"))
            system, msg = "STARTING", f"Starting: video {self._camera_state.lower()}, {detail}"
        else:
            system, msg = "RUNNING", ""

        if fault and not self._in_fault:
            log.error("FAULT: %s", reason)
            self._log_event(EventType.FAULT, details=reason)
        elif fault and reason != self._fault_reason:
            log.error("FAULT: %s", reason)
        elif not fault and self._in_fault and running:
            log.info("Fault cleared (%s)", self._fault_reason)
            self._log_event(EventType.FAULT_CLEARED, details=self._fault_reason)
        self._in_fault = fault if running else False
        self._fault_reason = reason if running else ""

        if area != self._area:
            self._area = area
            self.area_status.emit(area)
        if (system, msg) != (self._system, getattr(self, "_system_msg", "")):
            self._system = system
            self._system_msg = msg
            self.system_state.emit(system, msg)

        # ---- PLC output (only meaningful states; hold while STARTING)
        if running and area == "STARTING" and not fault:
            return
        if ai_mode:
            zone_occupied = self.event_state.zone_occupied()
            zone_devices = self.region_mapping.devices()
        else:
            last = self._last_result
            zone_occupied = dict(last.roi_occupied) if last else {}
            zone_devices = {r.id: r.plc_device for r in self.roi_manager.include_rois() if r.plc_device}
        out = PlcOutputState(
            running=running,
            area_occupied=bool(area_occupied) if running else False,
            camera_ok=camera_ok,
            ai_ok=detector_ok,
            fault=fault,
            fault_code=code,
            roi_occupied=zone_occupied,
            roi_devices=zone_devices,
        )
        if force_plc or out != self._last_output:
            self._last_output = out
            self.plc_worker.update_output(out)

    # ================================================================== helpers
    def _log_event(self, event_type: str, roi_id: str = "", roi_name: str = "", details: str = "", snapshot_path: str = "") -> None:
        rec = self.events.add(event_type, roi_id, roi_name, details, snapshot_path)
        if rec is not None:
            self.event_logged.emit(rec)

    def status_summary(self) -> Dict[str, str]:
        if self.ai_camera_mode:
            detector = self._event_channel
        else:
            detector = "RUNNING" if self._detecting else ("LOADED" if self._model_loaded else "STOPPED")
        return {
            "system": self._system,
            "area": self._area,
            "mode": self.detection_mode.value,
            "camera": self._camera_state,
            "ai": detector,
            "event_channel": self._event_channel,
            "plc": "CONNECTED" if self._plc_connected else "DISCONNECTED",
        }
