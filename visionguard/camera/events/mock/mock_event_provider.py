"""MockEventProvider: demo and test the whole chain without an AI camera.

The UI pushes simulated events (person enter / exit / camera disconnect) into this provider
and everything downstream - state machine, PLC, logging - behaves exactly as with a real camera.
"""
from __future__ import annotations

import logging
import queue
import time
from datetime import datetime
from typing import List, Optional

from ..base_event_provider import CameraEventProvider
from ..camera_event import CameraEvent, CameraEventType, TargetType

log = logging.getLogger("EVENT")


class MockEventProvider(CameraEventProvider):
    name = "Simulated AI camera"
    provider_type = "mock"

    def __init__(self, region_id: str = "1") -> None:
        super().__init__()
        self.default_region = region_id
        self._queue: "queue.Queue[CameraEvent]" = queue.Queue()
        self._fail_next_connect = False
        self._forced_down = False

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        if self._fail_next_connect or self._forced_down:
            self.last_error = "Simulated camera is disconnected"
            self._connected = False
            return False
        self._connected = True
        self.last_error = ""
        return True

    def disconnect(self) -> None:
        self._connected = False
        self._listening = False

    def start_listening(self) -> bool:
        if not self._connected and not self.connect():
            return False
        self._listening = True
        return True

    def stop_listening(self) -> None:
        self._listening = False

    def poll(self, timeout: float = 0.5) -> List[CameraEvent]:
        if not self._listening:
            return []
        events: List[CameraEvent] = []
        deadline = time.monotonic() + max(0.01, timeout)
        while time.monotonic() < deadline:
            try:
                event = self._queue.get(timeout=0.05)
            except queue.Empty:
                break
            if event.type_enum == CameraEventType.CAMERA_DISCONNECTED:
                self._forced_down = True
                self._connected = False
                self._listening = False
                self.last_error = "Simulated camera disconnect"
                events.append(event)
                break
            events.append(event)
        return events

    def health_check(self) -> bool:
        return self._connected and not self._forced_down

    def describe(self) -> str:
        return self.name

    # ------------------------------------------------------------------ simulation API
    @property
    def supports_simulation(self) -> bool:
        return True

    def simulate(self, event: CameraEvent) -> None:
        self._queue.put(event)

    def simulate_person_enter(self, region_id: Optional[str] = None) -> CameraEvent:
        event = CameraEvent(
            event_type=CameraEventType.REGION_ENTER.value, active=True, timestamp=datetime.now(),
            region_id=region_id or self.default_region, target_type=TargetType.HUMAN.value,
            description="Simulated: person entered the zone", raw_data={"source": "mock"})
        self.simulate(event)
        return event

    def simulate_person_exit(self, region_id: Optional[str] = None) -> CameraEvent:
        event = CameraEvent(
            event_type=CameraEventType.REGION_EXIT.value, active=True, timestamp=datetime.now(),
            region_id=region_id or self.default_region, target_type=TargetType.HUMAN.value,
            description="Simulated: person left the zone", raw_data={"source": "mock"})
        self.simulate(event)
        return event

    def simulate_intrusion(self, active: bool, region_id: Optional[str] = None) -> CameraEvent:
        etype = CameraEventType.INTRUSION_START if active else CameraEventType.INTRUSION_END
        event = CameraEvent(
            event_type=etype.value, active=active, timestamp=datetime.now(),
            region_id=region_id or self.default_region, target_type=TargetType.HUMAN.value,
            description=f"Simulated: intrusion {'active' if active else 'inactive'}",
            raw_data={"source": "mock"})
        self.simulate(event)
        return event

    def simulate_vehicle(self, region_id: Optional[str] = None) -> CameraEvent:
        """A non-human target: it must be visible in the log but must not trigger the PLC."""
        event = CameraEvent(
            event_type=CameraEventType.INTRUSION_START.value, active=True, timestamp=datetime.now(),
            region_id=region_id or self.default_region, target_type=TargetType.VEHICLE.value,
            description="Simulated: vehicle in the zone", raw_data={"source": "mock"})
        self.simulate(event)
        return event

    def simulate_disconnect(self) -> CameraEvent:
        event = CameraEvent.health(CameraEventType.CAMERA_DISCONNECTED, "Simulated camera disconnect")
        self.simulate(event)
        return event

    def simulate_reconnect(self) -> None:
        self._forced_down = False
        self._fail_next_connect = False
        self.last_error = ""
