"""Typed configuration schema (dataclasses) persisted as JSON in config/.

Files:
    config/app_config.json     -> AppConfig
    config/camera_config.json  -> CameraConfig
    config/ai_config.json      -> AIConfig (detector + logic/debounce)
    config/plc_config.json     -> PlcConfig
    config/roi_config.json     -> handled by roi.roi_manager (normalized points)
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import List


# --------------------------------------------------------------------------- enums
class DetectionMode(str, Enum):
    """Where the person detection happens."""
    AI_CAMERA = "ai_camera"   # the camera detects and sends events; the PC only shows video
    PC_YOLO = "pc_yolo"       # the PC runs YOLO on the video stream (legacy pipeline)

    @property
    def label(self) -> str:
        return {
            DetectionMode.AI_CAMERA: "AI Camera (camera detects)",
            DetectionMode.PC_YOLO: "PC AI / YOLO (software detects)",
        }[self]


class CameraBrand(str, Enum):
    HIKVISION = "hikvision"
    DAHUA = "dahua"
    GENERIC = "generic"
    USB = "usb"
    INDUSTRIAL = "industrial"

    @property
    def label(self) -> str:
        return {
            CameraBrand.HIKVISION: "Hikvision",
            CameraBrand.DAHUA: "Dahua",
            CameraBrand.GENERIC: "Generic (ONVIF / HTTP)",
            CameraBrand.USB: "USB Camera",
            CameraBrand.INDUSTRIAL: "Industrial Camera",
        }[self]


class EventProviderType(str, Enum):
    ISAPI = "isapi"                 # Hikvision /ISAPI/Event/notification/alertStream
    DAHUA_HTTP = "dahua_http"       # Dahua cgi-bin/eventManager.cgi?action=attach
    GENERIC_HTTP = "generic_http"   # any camera posting/streaming JSON or XML
    MOCK = "mock"                   # simulated events, no camera needed

    @property
    def label(self) -> str:
        return {
            EventProviderType.ISAPI: "Hikvision ISAPI alertStream",
            EventProviderType.DAHUA_HTTP: "Dahua HTTP eventManager",
            EventProviderType.GENERIC_HTTP: "Generic HTTP (JSON/XML)",
            EventProviderType.MOCK: "Simulated events (no camera)",
        }[self]


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
    frame_timeout_s: float = 5.0          # no frame for this long => camera lost (keep >= 1 s)
    delays_s: List[float] = field(default_factory=lambda: [1.0, 2.0, 5.0])
    max_attempts: int = 0                 # 0 = retry forever


@dataclass
class EventLogicConfig:
    """Debounce and interpretation rules for camera AI events."""
    on_delay_ms: int = 200            # event must stay active this long before OCCUPIED
    off_delay_ms: int = 800           # area must stay quiet this long before CLEAR
    clear_timeout_s: float = 5.0      # no re-trigger for this long = cleared (cameras that never send "inactive")
    line_cross_hold_s: float = 3.0    # a line crossing is a pulse: hold OCCUPIED this long (0 = ignore)
    use_person_count: bool = True     # count REGION_ENTER / REGION_EXIT per region
    strict_human_only: bool = False   # True: drop events whose target type is unknown
    fault_on_event_loss: bool = True  # event channel down -> FAULT (never a fake CLEAR)
    fault_on_video_loss: bool = True  # RTSP down -> FAULT as well
    unmapped_regions_count: bool = True   # regions without a mapping still drive the global area state


@dataclass
class AiCameraConfig:
    """An AI camera that detects people itself: RTSP for video, an event channel for alarms."""
    ip: str = "192.168.1.64"
    http_port: int = 80
    rtsp_port: int = 554
    username: str = "admin"
    password: str = ""
    use_https: bool = False
    rtsp_url: str = ""                 # empty -> built from ip / rtsp_port / channel
    rtsp_channel: str = "101"          # Hikvision: 101 main stream, 102 sub stream
    rtsp_transport: str = "tcp"
    event_provider: str = EventProviderType.ISAPI.value
    event_url: str = ""                # empty -> provider default path
    event_timeout_s: float = 2.0       # socket read timeout on the event stream
    health_interval_s: float = 30.0    # periodic device check on the event channel
    reconnect_delays_s: List[float] = field(default_factory=lambda: [1.0, 2.0, 5.0])
    channels: List[int] = field(default_factory=list)   # empty = accept every channel
    logic: EventLogicConfig = field(default_factory=EventLogicConfig)

    @property
    def provider_enum(self) -> EventProviderType:
        try:
            return EventProviderType(self.event_provider)
        except ValueError:
            return EventProviderType.ISAPI

    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        port = f":{self.http_port}" if self.http_port not in (80, 443) else ""
        return f"{scheme}://{self.ip}{port}"

    def stream_path(self, brand: str = CameraBrand.HIKVISION.value) -> str:
        """RTSP path for this camera.

        A channel that already starts with "/" is used verbatim, so any camera can be
        supported by typing its own path; otherwise the brand's usual template is used.
        """
        channel = (self.rtsp_channel or "101").strip()
        if channel.startswith("/"):
            return channel
        if brand == CameraBrand.DAHUA.value:
            # Dahua: 101 -> channel 1 main stream, 102 -> channel 1 sub stream
            main = channel[-2:] if len(channel) >= 3 else "01"
            cam = channel[:-2] if len(channel) >= 3 else channel
            subtype = 0 if main in ("01", "1") else 1
            return f"/cam/realmonitor?channel={cam or 1}&subtype={subtype}"
        return f"/Streaming/Channels/{channel}"

    def to_rtsp_config(self, brand: str = CameraBrand.HIKVISION.value) -> "RtspCameraConfig":
        """Video settings for the RTSP receiver, derived from the AI camera settings."""
        return RtspCameraConfig(
            url=self.rtsp_url.strip(),
            ip=self.ip,
            port=self.rtsp_port,
            username=self.username,
            password=self.password,
            path=self.stream_path(brand),
            transport=self.rtsp_transport,
        )


#: how many cameras one VisionGuard instance can watch at once
MAX_CAMERAS = 4


@dataclass
class ExtraCamera:
    """Camera 2..N: only the settings that differ from camera to camera.

    Detection mode, brand and the reconnect policy stay on CameraConfig and are shared by
    every camera in the group.
    """
    name: str = ""
    camera_type: str = CameraType.RTSP.value
    ai_camera: AiCameraConfig = field(default_factory=AiCameraConfig)
    usb: UsbCameraConfig = field(default_factory=UsbCameraConfig)
    video: VideoCameraConfig = field(default_factory=VideoCameraConfig)
    rtsp: RtspCameraConfig = field(default_factory=RtspCameraConfig)
    industrial: IndustrialCameraConfig = field(default_factory=IndustrialCameraConfig)


@dataclass
class CameraConfig:
    detection_mode: str = DetectionMode.AI_CAMERA.value   # AI camera first, YOLO stays available
    brand: str = CameraBrand.HIKVISION.value
    camera_type: str = CameraType.USB.value               # video source for PC AI / YOLO mode
    ai_camera: AiCameraConfig = field(default_factory=AiCameraConfig)
    usb: UsbCameraConfig = field(default_factory=UsbCameraConfig)
    video: VideoCameraConfig = field(default_factory=VideoCameraConfig)
    rtsp: RtspCameraConfig = field(default_factory=RtspCameraConfig)
    industrial: IndustrialCameraConfig = field(default_factory=IndustrialCameraConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    name: str = ""                                        # label on the video tile
    count: int = 1                                        # how many cameras watch the area
    extra: List[ExtraCamera] = field(default_factory=list)  # cameras 2..N

    @property
    def type_enum(self) -> CameraType:
        try:
            return CameraType(self.camera_type)
        except ValueError:
            return CameraType.USB

    @property
    def mode_enum(self) -> DetectionMode:
        try:
            return DetectionMode(self.detection_mode)
        except ValueError:
            return DetectionMode.AI_CAMERA

    @property
    def brand_enum(self) -> CameraBrand:
        try:
            return CameraBrand(self.brand)
        except ValueError:
            return CameraBrand.GENERIC

    @property
    def is_ai_camera(self) -> bool:
        return self.mode_enum == DetectionMode.AI_CAMERA

    # ------------------------------------------------------------------ multi camera
    @property
    def camera_count(self) -> int:
        """1..MAX_CAMERAS, whatever the file says."""
        return max(1, min(int(self.count or 1), MAX_CAMERAS))

    def set_count(self, count: int) -> None:
        """Grow or shrink the group, keeping the cameras that are already configured."""
        count = max(1, min(int(count), MAX_CAMERAS))
        self.count = count
        while len(self.extra) < count - 1:
            self.extra.append(ExtraCamera(camera_type=self.camera_type))
        del self.extra[count - 1:]

    def label(self, index: int) -> str:
        name = (self.name if index == 0 else self._extra(index).name).strip()
        return name or f"Camera {index + 1}"

    def _extra(self, index: int) -> ExtraCamera:
        slot = index - 1
        while len(self.extra) <= slot:
            self.extra.append(ExtraCamera(camera_type=self.camera_type))
        return self.extra[slot]

    def unit(self, index: int) -> "CameraConfig":
        """A standalone CameraConfig for camera `index` (0 = this one).

        Workers take a plain CameraConfig, so each camera in the group is handed its own
        copy with the shared settings folded in and its own source settings on top.
        """
        if index <= 0:
            unit = deepcopy(self)
        else:
            src = self._extra(index)
            unit = deepcopy(self)
            unit.camera_type = src.camera_type
            unit.ai_camera = deepcopy(src.ai_camera)
            unit.usb = deepcopy(src.usb)
            unit.video = deepcopy(src.video)
            unit.rtsp = deepcopy(src.rtsp)
            unit.industrial = deepcopy(src.industrial)
            unit.name = src.name
        unit.count = 1
        unit.extra = []
        return unit

    def units(self) -> List["CameraConfig"]:
        return [self.unit(i) for i in range(self.camera_count)]

    def describe_source(self) -> str:
        """One line naming where the picture comes from, for the live view header."""
        if self.is_ai_camera:
            brand = self.brand_enum.value.capitalize()
            host = self.ai_camera.ip or "chưa đặt IP"
            return f"{brand} RTSP · {host}"
        kind = self.type_enum
        if kind == CameraType.USB:
            return f"USB camera #{self.usb.device_index}"
        if kind == CameraType.VIDEO:
            name = self.video.path.replace(chr(92), "/").rsplit("/", 1)[-1]
            return f"Video file · {name}" if name else "Video file"
        if kind == CameraType.RTSP:
            # never echo rtsp.url here: a full URL carries the password
            return f"RTSP · {self.rtsp.ip}" if self.rtsp.ip else "RTSP (URL tuỳ chỉnh)"
        return kind.value.upper()


# --------------------------------------------------------------------------- AI
@dataclass
class DetectorConfig:
    model_path: str = "models/yolo11n.pt"
    confidence: float = 0.40
    iou: float = 0.50
    device: str = "auto"        # auto | cpu | cuda | cuda:0
    imgsz: int = 640
    max_fps: float = 12.0       # inferences per second PER CAMERA; 0 = as fast as it can
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
class ClipConfig:
    """Video of the stretch where a person was in the zone, one file per camera."""
    enabled: bool = False
    directory: str = "events/clips"
    pre_roll_s: float = 2.0        # seconds kept from BEFORE the zone tripped
    post_roll_s: float = 3.0       # keep rolling this long after it cleared
    max_duration_s: float = 120.0  # a stuck occupancy must not fill the disk
    fps: float = 0.0               # 0 = measure it from the camera
    scale: float = 0.5             # 1.0 full size; halving cuts the file and the RAM ring buffer
    retention_days: int = 14       # 0 = keep forever
    max_total_gb: float = 20.0     # hard cap on the whole clips folder; 0 = no cap


@dataclass
class RetentionConfig:
    """How long the things this system writes are kept. 0 disables a rule."""
    event_days: int = 90           # rows in the SQLite history
    event_max_rows: int = 200000   # hard cap regardless of age
    snapshot_days: int = 30
    log_days: int = 30
    log_dir: str = "logs"
    interval_hours: float = 6.0    # how often housekeeping runs while the app is up


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
    clip: ClipConfig = field(default_factory=ClipConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)
