import json

from visionguard.config.config_manager import ConfigManager, from_dict, to_dict
from visionguard.config.schemas import CameraConfig, CameraType, PlcConfig


def test_defaults_are_created(tmp_path):
    cm = ConfigManager(tmp_path)
    s = cm.load_all()
    assert (tmp_path / "camera_config.json").exists()
    assert s.plc.simulation_mode is True
    assert s.plc.mapping.device_person == "M100"
    assert s.ai.logic.on_delay_ms == 200 and s.ai.logic.off_delay_ms == 1000


def test_roundtrip_with_unknown_and_missing_keys():
    cfg = CameraConfig()
    data = to_dict(cfg)
    data["usb"]["device_index"] = 2
    data["camera_type"] = "rtsp"
    data["unknown_key"] = {"x": 1}
    del data["video"]
    back = from_dict(CameraConfig, data)
    assert back.usb.device_index == 2
    assert back.type_enum == CameraType.RTSP
    assert back.video.loop is True           # default filled
    assert back.reconnect.delays_s == [1.0, 2.0, 5.0]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "plc_config.json").write_text("{ not json", encoding="utf-8")
    cm = ConfigManager(tmp_path)
    s = cm.load_all()
    assert isinstance(s.plc, PlcConfig)
    assert cm.save("plc")
    assert json.loads((tmp_path / "plc_config.json").read_text(encoding="utf-8"))["mapping"]["device_heartbeat"] == "M110"
