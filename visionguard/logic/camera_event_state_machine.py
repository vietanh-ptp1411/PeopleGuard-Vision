"""Turn AI camera events into a debounced occupancy state per zone.

Cameras do not agree on how they report presence, so two styles are supported side by side:

  presence style   INTRUSION_START / PERSON_DETECTED keep the zone occupied until the matching
                   INTRUSION_END / PERSON_CLEARED arrives. Cameras repeat the active alarm while
                   a target stays inside, so a missing "end" is caught by `clear_timeout_s`.

  counting style   REGION_ENTER adds one person, REGION_EXIT removes one. The count is
                   authoritative and never times out: a missed EXIT leaves the zone OCCUPIED,
                   which is the safe direction. A missed ENTER cannot happen silently because
                   the camera would not report the EXIT either.

  LINE_CROSS is a pulse with no end at all, so it holds the zone occupied for `line_cross_hold_s`.

On top of that sits the same debounce used by the YOLO path: ON delay before OCCUPIED,
OFF delay before CLEAR, so a flickering camera never rattles the PLC bit.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from ..camera.events.camera_event import CameraEvent, CameraEventType
from ..config.schemas import EventLogicConfig
from .debounce import DebounceConfig, Debouncer

log = logging.getLogger("EVENT")


class ZoneState(str, Enum):
    UNKNOWN = "UNKNOWN"       # no event seen yet - the camera has not told us anything
    CLEAR = "CLEAR"
    OCCUPIED = "OCCUPIED"

    @property
    def occupied(self) -> bool:
        return self is ZoneState.OCCUPIED


@dataclass
class ZoneTransition:
    region_id: str
    occupied: bool
    timestamp: datetime = field(default_factory=datetime.now)
    reason: str = ""

    @property
    def became_occupied(self) -> bool:
        return self.occupied

    @property
    def became_clear(self) -> bool:
        return not self.occupied


@dataclass
class Zone:
    """Live state of one camera region."""
    region_id: str
    presence: bool = False                 # held by intrusion / person-detected style events
    person_count: int = 0                  # maintained by enter / exit style events
    pulse_until: float = 0.0               # line crossing hold
    last_trigger: float = 0.0              # monotonic time of the last presence refresh
    last_event: Optional[CameraEvent] = None
    seen: bool = False                     # any event ever received for this zone
    state: ZoneState = ZoneState.UNKNOWN   # debounced state

    def raw_present(self, now: float, use_count: bool) -> bool:
        if self.presence:
            return True
        if use_count and self.person_count > 0:
            return True
        return now < self.pulse_until


class CameraEventStateMachine:
    """Feed events in, read a debounced occupancy state out."""

    def __init__(self, config: Optional[EventLogicConfig] = None) -> None:
        self.config = config or EventLogicConfig()
        self._zones: Dict[str, Zone] = {}
        self._debouncers: Dict[str, Debouncer] = {}
        self._last_event: Optional[CameraEvent] = None
        self._ignored_count = 0

    # ------------------------------------------------------------------ configuration
    def set_config(self, config: EventLogicConfig) -> None:
        self.config = config
        for deb in self._debouncers.values():
            deb.set_config(self._debounce_config())

    def _debounce_config(self) -> DebounceConfig:
        # one event is enough to react; the timing is what filters flicker
        return DebounceConfig(self.config.on_delay_ms, self.config.off_delay_ms, min_detection_frames=1)

    def reset(self) -> None:
        self._zones.clear()
        self._debouncers.clear()
        self._last_event = None
        self._ignored_count = 0

    def forget_zone(self, region_id: str) -> None:
        self._zones.pop(str(region_id), None)
        self._debouncers.pop(str(region_id), None)

    # ------------------------------------------------------------------ input
    def handle_event(self, event: CameraEvent, now: Optional[float] = None) -> None:
        """Apply one camera event. Health events are ignored here (the worker owns them)."""
        etype = event.type_enum
        if etype.is_health or etype == CameraEventType.OTHER:
            return
        if event.is_non_human_target:
            self._ignored_count += 1
            return
        now = time.monotonic() if now is None else now
        zone = self._zone(event.region)
        zone.last_event = event
        zone.seen = True
        self._last_event = event

        if etype.starts_presence:
            if etype == CameraEventType.REGION_ENTER:
                reported = event.count if (event.count and event.count > 0) else None
                zone.person_count = reported if reported is not None else zone.person_count + 1
                log.debug("Region %s: person entered (count=%d)", zone.region_id, zone.person_count)
            else:
                zone.presence = True
            zone.last_trigger = now
        elif etype == CameraEventType.REGION_EXIT:
            zone.person_count = max(0, zone.person_count - 1)
            zone.last_trigger = now
            log.debug("Region %s: person left (count=%d)", zone.region_id, zone.person_count)
        elif etype.ends_presence:
            zone.presence = False
            zone.person_count = 0
            zone.pulse_until = 0.0
            zone.last_trigger = now
        elif etype == CameraEventType.LINE_CROSS:
            hold = float(self.config.line_cross_hold_s)
            if hold > 0:
                zone.pulse_until = now + hold
                zone.last_trigger = now

        # Start the debounce timer at the moment the camera event arrived, not at the next tick,
        # so the ON/OFF delays really are measured from the event.
        raw = zone.raw_present(now, self.config.use_person_count)
        self._debouncer(zone.region_id).update(raw, now)

    def handle_events(self, events: List[CameraEvent], now: Optional[float] = None) -> None:
        for event in events:
            self.handle_event(event, now)

    # ------------------------------------------------------------------ time
    def tick(self, now: Optional[float] = None) -> List[ZoneTransition]:
        """Advance timers and debouncers. Returns the zones whose state changed."""
        now = time.monotonic() if now is None else now
        timeout = float(self.config.clear_timeout_s)
        transitions: List[ZoneTransition] = []
        for region_id, zone in self._zones.items():
            # a camera that never sends "inactive": drop presence after the timeout.
            # counts are deliberately left alone - a missed EXIT must not fake a CLEAR.
            if zone.presence and timeout > 0 and (now - zone.last_trigger) > timeout:
                zone.presence = False
                log.info("Region %s: no alarm refresh for %.0fs - presence released", region_id, timeout)
            raw = zone.raw_present(now, self.config.use_person_count)
            deb = self._debouncer(region_id)
            output = deb.update(raw, now)
            if output:
                new_state = ZoneState.OCCUPIED
            elif zone.seen and not raw:
                new_state = ZoneState.CLEAR       # settled: nothing present any more
            else:
                new_state = zone.state            # an ON delay is pending, hold the current view
            if new_state != zone.state:
                previous, zone.state = zone.state, new_state
                # UNKNOWN -> CLEAR is the first confirmation that the zone is empty, not a change
                # in occupancy, so it updates the display without raising an event.
                if new_state != ZoneState.UNKNOWN and not (previous == ZoneState.UNKNOWN and not new_state.occupied):
                    transitions.append(ZoneTransition(
                        region_id=region_id, occupied=new_state.occupied,
                        reason=f"{previous.value} -> {new_state.value}"))
        return transitions

    # ------------------------------------------------------------------ views
    @property
    def area_occupied(self) -> bool:
        return any(z.state.occupied for z in self._zones.values())

    @property
    def area_state(self) -> ZoneState:
        if not self._zones or all(z.state == ZoneState.UNKNOWN for z in self._zones.values()):
            return ZoneState.UNKNOWN
        return ZoneState.OCCUPIED if self.area_occupied else ZoneState.CLEAR

    def zone_states(self) -> Dict[str, ZoneState]:
        return {rid: z.state for rid, z in self._zones.items()}

    def zone_occupied(self) -> Dict[str, bool]:
        return {rid: z.state.occupied for rid, z in self._zones.items()}

    def occupied_zone_ids(self) -> List[str]:
        return [rid for rid, z in self._zones.items() if z.state.occupied]

    def person_count(self, region_id: str) -> int:
        zone = self._zones.get(str(region_id))
        return zone.person_count if zone else 0

    def total_person_count(self) -> int:
        return sum(z.person_count for z in self._zones.values())

    def zone_ids(self) -> List[str]:
        return list(self._zones.keys())

    def zone(self, region_id: str) -> Optional[Zone]:
        return self._zones.get(str(region_id))

    @property
    def last_event(self) -> Optional[CameraEvent]:
        return self._last_event

    @property
    def ignored_non_human(self) -> int:
        return self._ignored_count

    # ------------------------------------------------------------------ internals
    def _zone(self, region_id: str) -> Zone:
        rid = str(region_id)
        zone = self._zones.get(rid)
        if zone is None:
            zone = Zone(region_id=rid)
            self._zones[rid] = zone
            log.info("Camera region %s seen for the first time", rid)
        return zone

    def _debouncer(self, region_id: str) -> Debouncer:
        deb = self._debouncers.get(region_id)
        if deb is None:
            deb = Debouncer(self._debounce_config())
            self._debouncers[region_id] = deb
        return deb
