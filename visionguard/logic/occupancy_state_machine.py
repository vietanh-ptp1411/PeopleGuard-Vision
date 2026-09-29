"""Per-ROI occupancy state machine on top of the Debouncer.

    CLEAR --detect--> PENDING_OCCUPIED --(ON delay & min frames)--> OCCUPIED
    OCCUPIED --lost--> PENDING_CLEAR --(OFF delay)--> CLEAR
    PENDING_CLEAR --detect again--> OCCUPIED   (short misses are held)
    PENDING_OCCUPIED --lost--> CLEAR           (flicker before confirmation is ignored)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from .debounce import DebounceConfig, Debouncer


class OccupancyState(str, Enum):
    CLEAR = "CLEAR"
    PENDING_OCCUPIED = "PENDING_OCCUPIED"
    OCCUPIED = "OCCUPIED"
    PENDING_CLEAR = "PENDING_CLEAR"

    @property
    def occupied(self) -> bool:
        return self in (OccupancyState.OCCUPIED, OccupancyState.PENDING_CLEAR)


class AreaStatus(str, Enum):
    """System-level area status shown in the UI and mapped to PLC."""
    CLEAR = "CLEAR"
    WARNING = "WARNING"       # somebody in a warning zone, nobody in an alarm zone
    OCCUPIED = "OCCUPIED"     # somebody in an alarm zone
    FAULT = "FAULT"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class StateTransition:
    roi_id: str
    old: OccupancyState
    new: OccupancyState
    timestamp: float            # time.time()

    @property
    def became_occupied(self) -> bool:
        return not self.old.occupied and self.new.occupied

    @property
    def became_clear(self) -> bool:
        return self.old.occupied and not self.new.occupied


class OccupancyStateMachine:
    def __init__(self, roi_id: str, config: Optional[DebounceConfig] = None) -> None:
        self.roi_id = roi_id
        self._deb = Debouncer(config)
        self._state = OccupancyState.CLEAR
        self._state_since = time.monotonic()
        self.last_raw = False

    # ------------------------------------------------------------------ properties
    @property
    def state(self) -> OccupancyState:
        return self._state

    @property
    def is_occupied(self) -> bool:
        return self._state.occupied

    def time_in_state(self, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        return now - self._state_since

    def pending_progress(self, now: Optional[float] = None) -> float:
        return self._deb.pending_progress(now)

    def set_config(self, config: DebounceConfig) -> None:
        self._deb.set_config(config)

    def reset(self) -> None:
        self._deb.reset(False)
        self._set_state(OccupancyState.CLEAR)

    # ------------------------------------------------------------------ update
    def update(self, detected: bool, now: Optional[float] = None) -> Optional[StateTransition]:
        """Feed one frame result. Returns a transition if the 4-state view changed."""
        now = time.monotonic() if now is None else now
        self.last_raw = bool(detected)
        out = self._deb.update(detected, now)
        if out:
            new = OccupancyState.PENDING_CLEAR if self._deb.pending else OccupancyState.OCCUPIED
        else:
            new = OccupancyState.PENDING_OCCUPIED if self._deb.pending else OccupancyState.CLEAR
        if new == self._state:
            return None
        old = self._state
        self._set_state(new, now)
        return StateTransition(self.roi_id, old, new, time.time())

    def _set_state(self, new: OccupancyState, now: Optional[float] = None) -> None:
        self._state = new
        self._state_since = time.monotonic() if now is None else now


class OccupancyTracker:
    """One state machine per watched ROI, one per zone level, plus a global 'any ROI' machine.

    The level machines ("alarm", "warning") are what the PLC bits follow. They are debounced
    on the raw union of their zones, the same way the global machine is, so a person who
    steps from one alarm zone straight into another keeps the ALARM bit up without a gap.
    """

    GLOBAL_ID = "__AREA__"
    LEVEL_PREFIX = "__LEVEL__"

    def __init__(self, config: Optional[DebounceConfig] = None) -> None:
        self.config = (config or DebounceConfig()).clamp()
        self._machines: Dict[str, OccupancyStateMachine] = {}
        self._levels: Dict[str, OccupancyStateMachine] = {}
        self._global = OccupancyStateMachine(self.GLOBAL_ID, self.config)

    def set_config(self, config: DebounceConfig) -> None:
        self.config = config.clamp()
        self._global.set_config(self.config)
        for m in self._machines.values():
            m.set_config(self.config)
        for m in self._levels.values():
            m.set_config(self.config)

    def sync_rois(self, roi_ids: List[str]) -> None:
        """Create machines for new ROIs, drop machines of deleted ROIs."""
        for rid in roi_ids:
            if rid not in self._machines:
                self._machines[rid] = OccupancyStateMachine(rid, self.config)
        for rid in list(self._machines):
            if rid not in roi_ids:
                del self._machines[rid]

    def update(self, roi_counts: Dict[str, int], now: Optional[float] = None,
               roi_levels: Optional[Dict[str, str]] = None) -> List[StateTransition]:
        """Feed one frame. `roi_levels` maps a ROI id to its level; a ROI without one is alarm."""
        now = time.monotonic() if now is None else now
        roi_levels = roi_levels or {}
        self.sync_rois(list(roi_counts.keys()))
        transitions: List[StateTransition] = []
        for rid, count in roi_counts.items():
            t = self._machines[rid].update(count > 0, now)
            if t:
                transitions.append(t)
        # Level machines. Every level that has ever been fed keeps its machine, so a level
        # whose last zone was just deleted still debounces down to CLEAR instead of vanishing
        # while OCCUPIED and leaving its PLC bit wherever it was.
        raw_by_level: Dict[str, bool] = {lvl: False for lvl in self._levels}
        for rid, count in roi_counts.items():
            lvl = roi_levels.get(rid, "alarm")
            raw_by_level[lvl] = raw_by_level.get(lvl, False) or count > 0
        for lvl, raw in raw_by_level.items():
            machine = self._levels.get(lvl)
            if machine is None:
                machine = self._levels[lvl] = OccupancyStateMachine(self.LEVEL_PREFIX + lvl, self.config)
            t = machine.update(raw, now)
            if t:
                transitions.append(t)
        any_raw = any(c > 0 for c in roi_counts.values())
        t = self._global.update(any_raw, now)
        if t:
            transitions.append(t)
        return transitions

    def reset(self) -> None:
        self._global.reset()
        for m in self._machines.values():
            m.reset()
        for m in self._levels.values():
            m.reset()

    @classmethod
    def is_aggregate_id(cls, roi_id: str) -> bool:
        """True for the ids of the global and level machines - they are not zones."""
        return roi_id == cls.GLOBAL_ID or roi_id.startswith(cls.LEVEL_PREFIX)

    # ------------------------------------------------------------------ views
    @property
    def area_occupied(self) -> bool:
        return self._global.is_occupied

    @property
    def area_state(self) -> OccupancyState:
        return self._global.state

    def area_progress(self) -> float:
        return self._global.pending_progress()

    def roi_states(self) -> Dict[str, OccupancyState]:
        return {rid: m.state for rid, m in self._machines.items()}

    def roi_occupied(self) -> Dict[str, bool]:
        return {rid: m.is_occupied for rid, m in self._machines.items()}

    # ------------------------------------------------------------------ levels
    def level_occupied(self, level: str) -> bool:
        m = self._levels.get(str(level))
        return m.is_occupied if m is not None else False

    def level_state(self, level: str) -> OccupancyState:
        m = self._levels.get(str(level))
        return m.state if m is not None else OccupancyState.CLEAR

    def level_states(self) -> Dict[str, OccupancyState]:
        return {lvl: m.state for lvl, m in self._levels.items()}
