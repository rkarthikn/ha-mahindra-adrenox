"""Remote door lock. Off unless the owner opts in and the endpoint is verified."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AdrenoxConfigEntry, AdrenoxCoordinator
from .entity import AdrenoxCommandEntity, commands_enabled

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdrenoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if not commands_enabled(coordinator, "lock", "unlock"):
        return
    async_add_entities(AdrenoxLock(coordinator, vin) for vin in coordinator.data.vehicles)


class AdrenoxLock(AdrenoxCommandEntity, LockEntity):
    def __init__(self, coordinator: AdrenoxCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "door_lock")

    @property
    def is_locked(self) -> bool | None:
        unlocked = self.status.get("unlocked") if self.status else None
        return None if unlocked is None else not unlocked

    async def async_lock(self, **kwargs: Any) -> None:
        await self._command("lock")

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._command("unlock")
