"""Typed configuration schema (dataclasses) persisted as JSON in config/.

Files:
    config/app_config.json     -> AppConfig
    config/camera_config.json  -> CameraConfig
    config/ai_config.json      -> AIConfig (detector + logic/debounce)
    config/plc_config.json     -> PlcConfig
    config/roi_config.json     -> handled by roi.roi_manager (normalized points)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


# --------------------------------------------------------------------------- enums
class CameraType(str, Enum):
    USB = "usb"
    VIDEO = "video"
    RTSP = "rtsp"
    BASLER = "basler"
    HIKROBOT = "hikrobot"
    GENICAM = "genicam"

    @property
    def label(self) -> str:
        return {
            CameraType.USB: "USB Camera",
            CameraType.VIDEO: "Video File",
            CameraType.RTSP: "RTSP / IP Camera",
            CameraType.BASLER: "Basler (pylon)",
            CameraType.HIKROBOT: "Hikrobot (MVS)",
            CameraType.GENICAM: "Generic GenICam (GenTL)",
        }[self]


class ContainmentMode(str, Enum):
    FOOT_POINT = "foot_point"
    CENTER_POINT = "center_point"
    INTERSECTION = "intersection"

    @property
    def label(self) -> str:
        return {
            ContainmentMode.FOOT_POINT: "Foot Point (bottom-center)",
            ContainmentMode.CENTER_POINT: "Center Point",
            ContainmentMode.INTERSECTION: "Intersection Percentage",
        }[self]


class SignalMode(str, Enum):
    SINGLE_BIT = "single_bit"   # Mode A: one bit  (person present)
    DUAL_BIT = "dual_bit"       # Mode B: person bit + clear bit (mutually exclusive)


class FaultPersonOutput(str, Enum):
    """What to do with the PERSON bit while the system is in FAULT."""
    HOLD = "hold"   # keep last value (never fake a CLEAR)
    ON = "on"       # force occupied (most conservative)
    OFF = "off"     # force off (only if PLC evaluates the FAULT bit itself)


class FrameFormat(str, Enum):
    BINARY = "binary"
    ASCII = "ascii"


# --------------------------------------------------------------------------- camera
@dataclass
class UsbCameraConfig:
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    backend: str = "auto"  # auto | dshow | msmf | v4l2


@dataclass
class VideoCameraConfig:
    path: str = ""
    loop: bool = True
    realtime: bool = True       # play at file FPS (False = as fast as possible)
    start_paused: bool = False


@dataclass
class RtspCameraConfig:
    url: str = ""               # full URL wins if not empty
    ip: str = "192.168.1.100"
    port: int = 554
    username: str = ""
    password: str = ""
    path: str = "/Streaming/Channels/101"
    transport: str = "tcp"      # tcp | udp
    open_timeout_ms: int = 5000
    read_timeout_ms: int = 5000
    vendor_preset: str = "generic"


@dataclass
class IndustrialCameraConfig:
    serial_number: str = ""     # empty = first device found
    exposure_us: float = 10000.0
    gain: float = 0.0
    frame_rate: float = 30.0
    trigger_mode: str = "off"   # off | on (software/hardware configured on camera)
    pixel_format: str = "auto"  # auto | BGR8 | Mono8 ...
    grab_timeout_ms: int = 2000
    cti_path: str = ""          # GenICam only: GenTL producer .cti file


@dataclass
class ReconnectConfig:
    enabled: bool = True
    frame_timeout_s: float = 5.0          # no frame for this long => camera lost
    delays_s: List[float] = field(default_factory=lambda: [1.0, 2.0, 5.0])
    max_attempts: int = 0                 # 0 = retry forever


@dataclass
class CameraConfig:
    camera_type: str = CameraType.USB.value
    usb: UsbCameraConfig = field(default_factory=UsbCameraConfig)
    video: VideoCameraConfig = field(default_factory=VideoCameraConfig)
    rtsp: RtspCameraConfig = field(default_factory=RtspCameraConfig)
    industrial: IndustrialCameraConfig = field(default_factory=IndustrialCameraConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)

    @property
    def type_enum(self) -> CameraType:
        try:
            return CameraType(self.camera_type)
        except ValueError:
            return CameraType.USB


# --------------------------------------------------------------------------- AI
@dataclass
class DetectorConfig:
    model_path: str = "models/yolo11n.pt"
    confidence: float = 0.40
    iou: float = 0.50
    device: str = "auto"        # auto | cpu | cuda | cuda:0
    imgsz: int = 640
    classes: List[int] = field(default_factory=lambda: [0])  # COCO person
    max_det: int = 50
    half: bool = False
    tracking_enabled: bool = False
    tracker: str = "bytetrack.yaml"
    auto_download: bool = True  # download the pretrained model if missing
    auto_load_on_start: bool = True


@dataclass
class LogicConfig:
    containment_mode: str = ContainmentMode.FOOT_POINT.value
    intersection_threshold: float = 0.30  # for INTERSECTION mode
    on_delay_ms: int = 200
    off_delay_ms: int = 1000
    min_detection_frames: int = 3

    @property
    def mode_enum(self) -> ContainmentMode:
        try:
            return ContainmentMode(self.containment_mode)
        except ValueError:
            return ContainmentMode.FOOT_POINT


@dataclass
class AIConfig:
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    logic: LogicConfig = field(default_factory=LogicConfig)


# --------------------------------------------------------------------------- PLC
@dataclass
class PlcConnectionConfig:
    plc_type: str = "mitsubishi"
    protocol: str = "mc_protocol"
    plc_series: str = "iQ-F (FX5U)"     # informational
    ip: str = "192.168.1.10"
    port: int = 5000
    frame: str = "3E"
    frame_format: str = FrameFormat.BINARY.value
    network_no: int = 0x00
    pc_no: int = 0xFF
    dest_module_io: int = 0x03FF
    dest_module_station: int = 0x00
    timeout_s: float = 2.0
    retry_count: int = 2
    auto_reconnect: bool = True
    reconnect_delays_s: List[float] = field(default_factory=lambda: [1.0, 2.0, 5.0])


@dataclass
class PlcMappingConfig:
    device_person: str = "M100"        # PERSON_PRESENT / AREA_OCCUPIED
    device_clear: str = "M101"         # AREA_CLEAR (dual-bit mode)
    device_camera_ok: str = "M102"     # Camera connected
    device_ai_running: str = "M103"    # AI running
    device_fault: str = "M104"         # System fault
    device_heartbeat: str = "M110"
    device_status_word: str = "D100"   # 0 CLEAR,1 OCCUPIED,2 CAMERA ERR,3 PLC ERR,4 AI ERR,5 STOPPED
    write_roi_devices: bool = True     # also write each ROI's own plc_device


@dataclass
class HeartbeatConfig:
    enabled: bool = True
    interval_ms: int = 500


@dataclass
class FailSafeConfig:
    person_output_on_fault: str = FaultPersonOutput.HOLD.value
    clear_off_on_fault: bool = True    # never claim AREA_CLEAR during a fault


@dataclass
class PlcConfig:
    simulation_mode: bool = True
    signal_mode: str = SignalMode.DUAL_BIT.value
    word_output_enabled: bool = True
    connection: PlcConnectionConfig = field(default_factory=PlcConnectionConfig)
    mapping: PlcMappingConfig = field(default_factory=PlcMappingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    failsafe: FailSafeConfig = field(default_factory=FailSafeConfig)


# --------------------------------------------------------------------------- app
@dataclass
class SnapshotConfig:
    enabled: bool = True
    directory: str = "events"
    jpeg_quality: int = 90


@dataclass
class VisualizationConfig:
    """All drawing colors live here, never in the logic layer."""
    color_roi_include: str = "#FFD400"
    color_roi_include_occupied: str = "#FF3B30"
    color_roi_exclude: str = "#8E8E93"
    color_roi_editing: str = "#00C7FF"
    color_person: str = "#34C759"
    color_person_in_roi: str = "#FF3B30"
    color_person_ignored: str = "#8E8E93"
    color_point: str = "#FFFFFF"
    line_width: int = 2
    show_confidence: bool = True
    show_ids: bool = True
    show_points: bool = True
    show_labels: bool = True


@dataclass
class AppConfig:
    log_level: str = "INFO"
    events_db_path: str = "events/events.db"
    ui_fps_limit: int = 30
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)
