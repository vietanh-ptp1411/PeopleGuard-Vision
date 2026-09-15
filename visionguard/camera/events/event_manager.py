"""EventManager: builds the right CameraEventProvider from the camera configuration.

Mirrors CameraManager on the video side: the worker owns the provider instance, this class
only knows how to create one and which providers are usable on this machine.
"""
from __future__ import annotations

import logging
from typing import Dict, Tuple

from ...config.schemas import AiCameraConfig, CameraBrand, EventProviderType
from .base_event_provider import CameraEventProvider
from .dahua.dahua_event_provider import DahuaEventProvider
from .generic_http_event_provider import GenericHttpEventProvider
from .hikvision.hikvision_event_provider import HikvisionEventProvider
from .mock.mock_event_provider import MockEventProvider

log = logging.getLogger("EVENT")


class EventManager:
    #: brand -> the event provider it normally uses
    BRAND_DEFAULT: Dict[CameraBrand, EventProviderType] = {
        CameraBrand.HIKVISION: EventProviderType.ISAPI,
        CameraBrand.DAHUA: EventProviderType.DAHUA_HTTP,
        CameraBrand.GENERIC: EventProviderType.GENERIC_HTTP,
        CameraBrand.USB: EventProviderType.MOCK,
        CameraBrand.INDUSTRIAL: EventProviderType.MOCK,
    }

    def create_provider(self, config: AiCameraConfig, brand: CameraBrand = CameraBrand.HIKVISION) -> CameraEventProvider:
        provider_type = config.provider_enum
        if provider_type == EventProviderType.MOCK:
            return MockEventProvider()
        if provider_type == EventProviderType.ISAPI:
            return HikvisionEventProvider(config)
        if provider_type == EventProviderType.DAHUA_HTTP:
            return DahuaEventProvider(config)
        if provider_type == EventProviderType.GENERIC_HTTP:
            return GenericHttpEventProvider(config)
        log.warning("Unknown event provider '%s' for brand %s - using the simulated one",
                    config.event_provider, brand.value)
        return MockEventProvider()

    def default_provider_for(self, brand: CameraBrand) -> EventProviderType:
        return self.BRAND_DEFAULT.get(brand, EventProviderType.GENERIC_HTTP)

    def availability(self) -> Dict[EventProviderType, Tuple[bool, str]]:
        """provider -> (usable here, reason) so the UI can explain a greyed out choice."""
        return {
            EventProviderType.ISAPI: (HikvisionEventProvider.available(), HikvisionEventProvider.status()),
            EventProviderType.DAHUA_HTTP: (DahuaEventProvider.available(), DahuaEventProvider.status()),
            EventProviderType.GENERIC_HTTP: (GenericHttpEventProvider.available(), GenericHttpEventProvider.status()),
            EventProviderType.MOCK: (True, "OK"),
        }
