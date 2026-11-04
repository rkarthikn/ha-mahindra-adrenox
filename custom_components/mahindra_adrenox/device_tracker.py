"""The car's last reported GPS position."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AdrenoxConfigEntry, AdrenoxCoordinator
from .entity import AdrenoxEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdrenoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    # Created for every car: a car parked without a fix still has a tracker.
    async_add_entities(AdrenoxTracker(coordinator, vin) for vin in coordinator.data.vehicles)


class AdrenoxTracker(AdrenoxEntity, TrackerEntity):
    _attr_name = None  # use the device name, like phones do

    def __init__(self, coordinator: AdrenoxCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "location")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        pos = self.status.position if self.status else None
        return pos.latitude if pos else None

    @property
    def longitude(self) -> float | None:
        pos = self.status.position if self.status else None
        return pos.longitude if pos else None

    @property
    def location_accuracy(self) -> float:
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pos = self.status.position if self.status else None
        if pos is None:
            return {}
        return {"heading": pos.heading, "speed": pos.speed}
