"""CameraEventWorker and the region mapping: the pieces that sit between provider and PLC."""
import time

import pytest
from PySide6.QtCore import QCoreApplication

from visionguard.camera.events.camera_event import CameraEventType
from visionguard.camera.events.event_manager import EventManager
from visionguard.config.region_mapping import RegionMapping, RegionMappingManager
from visionguard.config.schemas import AiCameraConfig, CameraBrand, EventProviderType
from visionguard.workers.camera_event_worker import CameraEventWorker, EventChannelState


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(app, cond, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError(f"timeout waiting for {what}")


@pytest.fixture
def worker(qapp):
    config = AiCameraConfig(event_provider=EventProviderType.MOCK.value)
    w = CameraEventWorker(EventManager(), config, CameraBrand.HIKVISION)
    w.start()
    yield w
    w.stop_worker()
    w.wait(3000)


def test_worker_connects_and_forwards_events(qapp, worker):
    events, states = [], []
    worker.event_received.connect(events.append)
    worker.state_changed.connect(lambda s, m: states.append(s))

    worker.request_connect()
    wait(qapp, lambda: worker.state == EventChannelState.ONLINE, 5, "ONLINE")
    assert worker.online
    wait(qapp, lambda: any(e.type_enum == CameraEventType.CAMERA_CONNECTED for e in events), 3, "connected event")

    worker.simulate("enter", "3")
    wait(qapp, lambda: any(e.type_enum == CameraEventType.REGION_ENTER for e in events), 5, "enter event")
    entered = [e for e in events if e.type_enum == CameraEventType.REGION_ENTER][0]
    assert entered.region == "3" and entered.is_human
    assert worker.events_seen >= 1

    worker.simulate("vehicle", "3")
    wait(qapp, lambda: any(e.is_non_human_target for e in events), 5, "vehicle event")

    worker.request_disconnect()
    wait(qapp, lambda: worker.state == EventChannelState.DISCONNECTED, 5, "DISCONNECTED")


def test_worker_reports_a_lost_channel(qapp, worker):
    events = []
    worker.event_received.connect(events.append)
    worker.request_connect()
    wait(qapp, lambda: worker.online, 5, "ONLINE")

    worker.simulate("disconnect")
    wait(qapp, lambda: worker.state in (EventChannelState.RECONNECTING, EventChannelState.ERROR), 5, "channel lost")
    assert any(e.type_enum == CameraEventType.CAMERA_DISCONNECTED for e in events)
    assert not worker.online, "a lost channel must never look online"

    # the channel stays down (the simulated camera is still unplugged) and keeps retrying
    pump(qapp, 0.4)
    assert worker.state in (EventChannelState.RECONNECTING, EventChannelState.ERROR)

    worker.simulate("reconnect")
    worker.request_connect()
    wait(qapp, lambda: worker.online, 6, "back ONLINE")


def test_worker_disable_for_yolo_mode(qapp, worker):
    worker.request_connect()
    wait(qapp, lambda: worker.online, 5, "ONLINE")
    worker.request_disable()
    wait(qapp, lambda: worker.state == EventChannelState.DISABLED, 5, "DISABLED")
    assert not worker.online


def test_worker_survives_a_broken_provider(qapp):
    """A camera that cannot be reached must leave the worker alive and retrying."""
    config = AiCameraConfig(ip="127.0.0.1", http_port=1, event_provider=EventProviderType.ISAPI.value,
                            event_timeout_s=0.5, reconnect_delays_s=[0.2])
    w = CameraEventWorker(EventManager(), config, CameraBrand.HIKVISION)
    w.start()
    try:
        states = []
        w.state_changed.connect(lambda s, m: states.append(s))
        w.request_connect()
        wait(qapp, lambda: w.state in (EventChannelState.ERROR, EventChannelState.RECONNECTING), 15,
             "connection failure reported")
        assert not w.online
        assert w.isRunning(), "the worker thread must stay alive after a failed connection"
    finally:
        w.stop_worker()
        assert w.wait(5000)


# ============================================================== region mapping
def test_region_mapping_roundtrip(tmp_path):
    path = tmp_path / "region_mapping.json"
    m = RegionMappingManager(path)
    m.upsert(RegionMapping("1", "Robot Zone", "M100"))
    m.upsert(RegionMapping("2", "Loading Zone", "M101"))
    assert m.devices() == {"1": "M100", "2": "M101"}
    assert m.name_for("1") == "Robot Zone"
    assert m.device_for("2") == "M101"
    assert m.save()

    again = RegionMappingManager(path)
    assert again.load() and len(again) == 2
    assert again.get("2").name == "Loading Zone"

    again.upsert(RegionMapping("2", "Loading Zone B", "M102"))
    assert len(again) == 2 and again.device_for("2") == "M102"

    disabled = RegionMapping("3", "Off zone", "M103", enabled=False)
    again.upsert(disabled)
    assert "3" not in again.devices(), "disabled regions must not drive a PLC device"
    assert again.device_for("3") == ""

    assert again.delete("1") and not again.delete("1")
    assert len(again) == 2


def test_region_mapping_auto_discovery(tmp_path):
    m = RegionMappingManager(tmp_path / "region_mapping.json")
    created = m.ensure("5")
    assert created.camera_region_id == "5" and created.plc_device == ""
    assert m.ensure("5") is not None and len(m) == 1, "ensure() must not duplicate a known region"
    assert m.name_for("5") == "Region 5"


def test_region_mapping_bad_file(tmp_path):
    path = tmp_path / "region_mapping.json"
    path.write_text("{ broken", encoding="utf-8")
    m = RegionMappingManager(path)
    assert m.load() is False and len(m) == 0
