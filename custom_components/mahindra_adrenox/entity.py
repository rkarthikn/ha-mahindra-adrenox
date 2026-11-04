"""Base entity: one device per vehicle (VIN)."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import endpoints as ep
from .api import AdrenoxAuthError, AdrenoxError
from .const import CONF_REMOTE_COMMANDS, DOMAIN, MANUFACTURER
from .coordinator import AdrenoxCoordinator
from .parser import VehicleStatus


class AdrenoxEntity(CoordinatorEntity[AdrenoxCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: AdrenoxCoordinator, vin: str, key: str) -> None:
        super().__init__(coordinator)
        self.vin = vin
        self._attr_unique_id = f"{vin}_{key}"
        self._attr_translation_key = key
        vehicle = coordinator.data.vehicles[vin]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vin)},
            name=vehicle.name or vehicle.registration or vehicle.model or "Mahindra",
            manufacturer=MANUFACTURER,
            model=vehicle.model,
            serial_number=vin,
        )

    @property
    def status(self) -> VehicleStatus | None:
        return self.coordinator.data.status.get(self.vin)

    @property
    def available(self) -> bool:
        return super().available and self.status is not None


def commands_enabled(coordinator: AdrenoxCoordinator, *commands: str) -> bool:
    """Remote commands need the owner's opt-in and a verified endpoint."""
    return bool(coordinator.config_entry.options.get(CONF_REMOTE_COMMANDS)) and all(
        ep.command_ready(c) for c in commands
    )


class AdrenoxCommandEntity(AdrenoxEntity):
    async def _command(self, command: str) -> None:
        try:
            await self.coordinator.api.command(self.vin, command)
        except AdrenoxAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("AdrenoX session expired; sign in again") from err
        except AdrenoxError as err:
            raise HomeAssistantError(f"AdrenoX did not accept the command: {err}") from err
        self.coordinator.refresh_after_command()
