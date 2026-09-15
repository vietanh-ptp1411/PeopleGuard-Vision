from visionguard.logic.debounce import DebounceConfig, Debouncer
from visionguard.logic.occupancy_state_machine import OccupancyState, OccupancyStateMachine, OccupancyTracker

CFG = DebounceConfig(on_delay_ms=200, off_delay_ms=1000, min_detection_frames=3)
DT = 1 / 30  # 30 fps


def run(sm: OccupancyStateMachine, values, t0=0.0):
    t = t0
    transitions = []
    for v in values:
        tr = sm.update(v, t)
        if tr:
            transitions.append(tr)
        t += DT
    return t, transitions


def test_on_delay_requires_continuous_detection():
    sm = OccupancyStateMachine("R", CFG)
    t, _ = run(sm, [True] * 3)            # 3 frames = ~67 ms < 200 ms
    assert sm.state == OccupancyState.PENDING_OCCUPIED
    t, tr = run(sm, [True] * 5, t)         # now ~ 267 ms and >= 3 frames
    assert sm.state == OccupancyState.OCCUPIED
    assert any(x.became_occupied for x in tr)


def test_flicker_before_confirmation_resets():
    sm = OccupancyStateMachine("R", CFG)
    t, _ = run(sm, [True] * 4)
    t, _ = run(sm, [False], t)
    assert sm.state == OccupancyState.CLEAR
    assert not sm.is_occupied


def test_short_miss_is_held_then_off_delay_clears():
    sm = OccupancyStateMachine("R", CFG)
    t, _ = run(sm, [True] * 10)
    assert sm.state == OccupancyState.OCCUPIED
    t, _ = run(sm, [False, False], t)      # YOLO misses 2 frames
    assert sm.state == OccupancyState.PENDING_CLEAR
    assert sm.is_occupied                  # PLC bit must stay ON
    t, _ = run(sm, [True], t)
    assert sm.state == OccupancyState.OCCUPIED
    t, tr = run(sm, [False] * 35, t)       # > 1000 ms without person
    assert sm.state == OccupancyState.CLEAR
    assert any(x.became_clear for x in tr)


def test_min_frames_dominates_when_fps_is_low():
    cfg = DebounceConfig(on_delay_ms=0, off_delay_ms=0, min_detection_frames=3)
    d = Debouncer(cfg)
    assert d.update(True, 0.0) is False
    assert d.update(True, 1.0) is False
    assert d.update(True, 2.0) is True
    assert d.update(False, 3.0) is False  # off delay 0 -> immediate


def test_tracker_global_and_per_roi():
    tr = OccupancyTracker(CFG)
    t = 0.0
    for _ in range(10):
        tr.update({"ROI_001": 1, "ROI_002": 0}, t)
        t += DT
    assert tr.area_occupied
    assert tr.roi_occupied() == {"ROI_001": True, "ROI_002": False}
    tr.sync_rois(["ROI_002"])
    assert "ROI_001" not in tr.roi_states()
    tr.reset()
    assert not tr.area_occupied
