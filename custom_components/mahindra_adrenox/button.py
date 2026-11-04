"""Remote climate start/stop. Off unless the owner opts in and it is verified."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AdrenoxConfigEntry, AdrenoxCoordinator
from .entity import AdrenoxCommandEntity, commands_enabled

PARALLEL_UPDATES = 1

COMMANDS = ("climate_start", "climate_stop")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdrenoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AdrenoxButton(coordinator, vin, command)
        for vin in coordinator.data.vehicles
        for command in COMMANDS
        if commands_enabled(coordinator, command)
    )


class AdrenoxButton(AdrenoxCommandEntity, ButtonEntity):
    def __init__(self, coordinator: AdrenoxCoordinator, vin: str, command: str) -> None:
        super().__init__(coordinator, vin, command)
        self.command = command

    async def async_press(self) -> None:
        await self._command(self.command)
