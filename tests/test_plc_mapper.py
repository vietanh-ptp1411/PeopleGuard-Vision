from person_safety_vision.config.schemas import FaultPersonOutput, PlcConfig, SignalMode
from person_safety_vision.plc.plc_manager import (WORD_AI_ERROR, WORD_CAMERA_ERROR, WORD_CLEAR, WORD_NOT_RUNNING,
                                                  WORD_OCCUPIED, PlcManager, PlcOutputMapper, PlcOutputState)


def state(**kw) -> PlcOutputState:
    base = dict(running=True, area_occupied=False, camera_ok=True, ai_ok=True, fault=False, fault_code=0)
    base.update(kw)
    return PlcOutputState(**base)


def test_dual_bit_mutually_exclusive():
    cfg = PlcConfig()
    occ = PlcOutputMapper.map(state(area_occupied=True), cfg)
    clr = PlcOutputMapper.map(state(area_occupied=False), cfg)
    assert occ["M100"] == 1 and occ["M101"] == 0 and occ["D100"] == WORD_OCCUPIED
    assert clr["M100"] == 0 and clr["M101"] == 1 and clr["D100"] == WORD_CLEAR
    assert occ["M102"] == 1 and occ["M103"] == 1 and occ["M104"] == 0


def test_single_bit_mode_has_no_clear_device():
    cfg = PlcConfig(signal_mode=SignalMode.SINGLE_BIT.value)
    out = PlcOutputMapper.map(state(area_occupied=True), cfg)
    assert out["M100"] == 1 and "M101" not in out


def test_fault_never_fakes_clear():
    cfg = PlcConfig()
    out = PlcOutputMapper.map(state(camera_ok=False, fault=True, fault_code=WORD_CAMERA_ERROR), cfg)
    assert out["M100"] is None            # hold
    assert out["M101"] == 0               # never claim clear
    assert out["M104"] == 1 and out["M102"] == 0 and out["D100"] == WORD_CAMERA_ERROR
    cfg.failsafe.person_output_on_fault = FaultPersonOutput.ON.value
    assert PlcOutputMapper.map(state(fault=True, fault_code=WORD_AI_ERROR), cfg)["M100"] == 1


def test_stopped_state():
    out = PlcOutputMapper.map(state(running=False), PlcConfig())
    assert out["M100"] is None and out["M101"] == 0 and out["M103"] == 0 and out["D100"] == WORD_NOT_RUNNING


def test_roi_devices():
    cfg = PlcConfig()
    st = state(roi_occupied={"ROI_001": True, "ROI_002": False}, roi_devices={"ROI_001": "M200", "ROI_002": "M201"})
    out = PlcOutputMapper.map(st, cfg)
    assert out["M200"] == 1 and out["M201"] == 0
    cfg.mapping.write_roi_devices = False
    assert "M200" not in PlcOutputMapper.map(st, cfg)


def test_manager_writes_only_on_change_and_heartbeat():
    cfg = PlcConfig()  # simulation
    pm = PlcManager(cfg)
    assert pm.simulated and pm.connect()
    first = pm.apply_state(state(area_occupied=False))
    assert ("M101", 1) in first and ("M100", 0) in first
    assert pm.apply_state(state(area_occupied=False)) == []            # no change -> no write
    second = pm.apply_state(state(area_occupied=True))
    assert sorted(second) == [("D100", 1), ("M100", 1), ("M101", 0)]    # only changed devices
    assert pm.heartbeat_tick(0.0) is True
    assert pm.heartbeat_tick(0.1) is None                               # not due yet
    assert pm.heartbeat_tick(1.0) is False                              # toggled back
    mem = pm.memory_snapshot()
    assert mem["M100"] == 1 and mem["M110"] == 0 and mem["D100"] == 1
    resynced = pm.resync()
    assert len(resynced) >= 6
