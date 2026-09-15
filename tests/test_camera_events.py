"""AI Camera mode: event parsing and the event driven occupancy state machine."""
from datetime import datetime

import pytest

from visionguard.camera.events.camera_event import CameraEvent, CameraEventType, TargetType
from visionguard.camera.events.hikvision.hikvision_event_parser import (HikvisionEventParser,
                                                                        HikvisionStreamParser)
from visionguard.camera.events.mock.mock_event_provider import MockEventProvider
from visionguard.config.schemas import EventLogicConfig
from visionguard.logic.camera_event_state_machine import CameraEventStateMachine, ZoneState


def alert(event_type: str, state: str = "active", region: str = "1", target: str = "human",
          channel: int = 1) -> bytes:
    region_block = (f"<DetectionRegionList><DetectionRegionEntry><regionID>{region}</regionID>"
                    f"<detectionTarget>{target}</detectionTarget></DetectionRegionEntry>"
                    f"</DetectionRegionList>") if region else ""
    return (f'--boundary\r\nContent-Type: application/xml\r\n\r\n'
            f'<EventNotificationAlert version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">'
            f"<channelID>{channel}</channelID><dateTime>2026-09-15T14:23:15+07:00</dateTime>"
            f"<activePostCount>1</activePostCount><eventType>{event_type}</eventType>"
            f"<eventState>{state}</eventState><eventDescription>{event_type} alarm</eventDescription>"
            f"{region_block}</EventNotificationAlert>\r\n").encode()


def parse(raw: bytes, **kwargs):
    stream, parser = HikvisionStreamParser(), HikvisionEventParser(**kwargs)
    return [e for payload in stream.feed(raw) for e in parser.parse(payload)]


# ============================================================== parser
def test_field_detection_maps_to_intrusion():
    events = parse(alert("fielddetection", "active", region="2"))
    assert len(events) == 1
    ev = events[0]
    assert ev.type_enum == CameraEventType.INTRUSION_START
    assert ev.active and ev.region == "2" and ev.is_human and ev.channel == 1
    end = parse(alert("fielddetection", "inactive"))[0]
    assert end.type_enum == CameraEventType.INTRUSION_END and not end.active


def test_region_enter_exit_and_line_cross():
    assert parse(alert("regionEntrance"))[0].type_enum == CameraEventType.REGION_ENTER
    assert parse(alert("regionExiting"))[0].type_enum == CameraEventType.REGION_EXIT
    assert parse(alert("linedetection"))[0].type_enum == CameraEventType.LINE_CROSS
    # the "inactive" side of a momentary event carries no meaning and is dropped
    assert parse(alert("regionEntrance", "inactive")) == []


def test_non_human_targets_are_dropped():
    assert parse(alert("fielddetection", target="vehicle")) == []
    assert parse(alert("fielddetection", target="animal")) == []
    unknown = parse(alert("fielddetection", target=""))
    assert len(unknown) == 1 and unknown[0].target_enum == TargetType.UNKNOWN
    assert parse(alert("fielddetection", target=""), strict_human_only=True) == []


def test_motion_and_keepalive_are_ignored():
    assert parse(alert("VMD")) == []                       # motion is not person specific
    assert parse(alert("videoloss", "inactive")) == []      # Hikvision keepalive
    lost = parse(alert("videoloss", "active", region=""))
    assert len(lost) == 1 and lost[0].type_enum == CameraEventType.CAMERA_ERROR


def test_stream_parser_handles_split_and_multiple_payloads():
    raw = alert("fielddetection") + alert("regionEntrance")
    stream, parser = HikvisionStreamParser(), HikvisionEventParser()
    payloads = []
    for i in range(0, len(raw), 37):          # arbitrary chunk size, splits tags in half
        payloads += stream.feed(raw[i:i + 37])
    events = [e for p in payloads for e in parser.parse(p)]
    assert [e.type_enum for e in events] == [CameraEventType.INTRUSION_START, CameraEventType.REGION_ENTER]


def test_multiple_regions_in_one_alert():
    raw = (b'<EventNotificationAlert><channelID>1</channelID><eventType>fielddetection</eventType>'
           b'<eventState>active</eventState><DetectionRegionList>'
           b'<DetectionRegionEntry><regionID>1</regionID><detectionTarget>human</detectionTarget></DetectionRegionEntry>'
           b'<DetectionRegionEntry><regionID>3</regionID><detectionTarget>human</detectionTarget></DetectionRegionEntry>'
           b'</DetectionRegionList></EventNotificationAlert>')
    events = parse(raw)
    assert [e.region for e in events] == ["1", "3"]


def test_json_payload():
    raw = (b'{"channelID":1,"eventType":"regionEntrance","eventState":"active",'
           b'"targetType":"human","DetectionRegionList":[{"regionID":"5"}]}')
    events = parse(raw)
    assert len(events) == 1 and events[0].region == "5"
    assert events[0].type_enum == CameraEventType.REGION_ENTER


def test_garbage_never_raises():
    assert parse(b"<EventNotificationAlert>broken xml") == []
    stream = HikvisionStreamParser()
    assert stream.feed(b"") == []
    assert HikvisionEventParser().parse("") == []
    assert HikvisionEventParser().parse("{not json}") == []


# ============================================================== state machine
CFG = EventLogicConfig(on_delay_ms=200, off_delay_ms=800, clear_timeout_s=5.0)


def event(etype: CameraEventType, region="1", target="human", count=None) -> CameraEvent:
    return CameraEvent(event_type=etype.value, active=True, timestamp=datetime.now(),
                       region_id=region, target_type=target, count=count)


def test_intrusion_presence_with_debounce():
    sm = CameraEventStateMachine(CFG)
    t = 0.0
    sm.handle_event(event(CameraEventType.INTRUSION_START), t)
    assert sm.tick(t) == []                       # ON delay not elapsed yet
    assert sm.area_state == ZoneState.UNKNOWN
    t += 0.25
    tr = sm.tick(t)
    assert [x.occupied for x in tr] == [True] and sm.area_occupied
    # the camera keeps repeating "active"; nothing changes
    for _ in range(3):
        t += 1.0
        sm.handle_event(event(CameraEventType.INTRUSION_START), t)
        assert sm.tick(t) == []
    sm.handle_event(event(CameraEventType.INTRUSION_END), t)
    assert sm.tick(t) == []                       # OFF delay holds the bit
    t += 0.9
    tr = sm.tick(t)
    assert [x.occupied for x in tr] == [False] and not sm.area_occupied
    assert sm.zone_states()["1"] == ZoneState.CLEAR


def test_missing_inactive_is_released_by_clear_timeout():
    sm = CameraEventStateMachine(CFG)
    t = 0.0
    sm.handle_event(event(CameraEventType.INTRUSION_START), t)
    t += 0.3
    sm.tick(t)
    assert sm.area_occupied
    t += 6.0                                      # camera went silent, no "inactive" ever came
    sm.tick(t)
    t += 0.9
    sm.tick(t)
    assert not sm.area_occupied


def test_person_counting_two_people():
    sm = CameraEventStateMachine(CFG)
    t = 0.0
    sm.handle_event(event(CameraEventType.REGION_ENTER), t)      # person A
    t += 0.3
    sm.tick(t)
    assert sm.area_occupied and sm.person_count("1") == 1
    sm.handle_event(event(CameraEventType.REGION_ENTER), t)      # person B
    sm.tick(t)
    assert sm.person_count("1") == 2
    sm.handle_event(event(CameraEventType.REGION_EXIT), t)       # A leaves, B stays
    t += 1.0
    assert sm.tick(t) == []                                       # still occupied
    assert sm.area_occupied and sm.person_count("1") == 1
    sm.handle_event(event(CameraEventType.REGION_EXIT), t)       # B leaves
    t += 0.9
    tr = sm.tick(t)
    assert [x.occupied for x in tr] == [False]
    assert sm.person_count("1") == 0
    # a count is never cleared by the timeout, only by an EXIT
    sm.handle_event(event(CameraEventType.REGION_ENTER), t)
    t += 0.3
    sm.tick(t)
    t += 60.0
    sm.tick(t)
    assert sm.area_occupied, "a missed EXIT must keep the zone OCCUPIED, never fake a CLEAR"


def test_two_zones_are_independent():
    sm = CameraEventStateMachine(CFG)
    t = 0.0
    sm.handle_event(event(CameraEventType.INTRUSION_START, region="1"), t)
    t += 0.3
    sm.tick(t)
    assert sm.occupied_zone_ids() == ["1"]
    sm.handle_event(event(CameraEventType.INTRUSION_START, region="2"), t)
    t += 0.3
    sm.tick(t)
    assert sorted(sm.occupied_zone_ids()) == ["1", "2"]
    sm.handle_event(event(CameraEventType.INTRUSION_END, region="1"), t)
    t += 0.9
    sm.tick(t)
    assert sm.occupied_zone_ids() == ["2"] and sm.area_occupied


def test_line_cross_pulse():
    sm = CameraEventStateMachine(EventLogicConfig(on_delay_ms=0, off_delay_ms=0, line_cross_hold_s=2.0))
    t = 0.0
    sm.handle_event(event(CameraEventType.LINE_CROSS), t)
    sm.tick(t)
    assert sm.area_occupied
    t += 2.5
    sm.tick(t)
    assert not sm.area_occupied


def test_vehicle_never_reaches_the_zone():
    sm = CameraEventStateMachine(CFG)
    sm.handle_event(event(CameraEventType.INTRUSION_START, target="vehicle"), 0.0)
    sm.tick(0.5)
    assert not sm.area_occupied and sm.ignored_non_human == 1
    assert sm.zone_ids() == []


# ============================================================== mock provider
def test_mock_provider_drives_the_state_machine():
    provider = MockEventProvider()
    assert provider.connect() and provider.start_listening()
    assert provider.supports_simulation
    sm = CameraEventStateMachine(CFG)
    provider.simulate_person_enter()
    sm.handle_events(provider.poll(0.2), 0.0)
    sm.tick(0.3)
    assert sm.area_occupied
    provider.simulate_person_exit()
    sm.handle_events(provider.poll(0.2), 0.3)
    sm.tick(1.3)
    assert not sm.area_occupied
    provider.simulate_disconnect()
    events = provider.poll(0.2)
    assert events and events[0].type_enum == CameraEventType.CAMERA_DISCONNECTED
    assert not provider.is_connected() and not provider.health_check()


def test_mock_vehicle_is_visible_but_ignored():
    provider = MockEventProvider()
    provider.connect()
    provider.start_listening()
    provider.simulate_vehicle()
    events = provider.poll(0.2)
    assert len(events) == 1 and events[0].is_non_human_target
    sm = CameraEventStateMachine(CFG)
    sm.handle_events(events, 0.0)
    sm.tick(1.0)
    assert not sm.area_occupied
