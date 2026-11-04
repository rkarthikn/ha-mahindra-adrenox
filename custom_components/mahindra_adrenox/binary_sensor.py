"""Doors, windows, lock, ignition and climate states."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AdrenoxConfigEntry, AdrenoxCoordinator
from .entity import AdrenoxEntity

PARALLEL_UPDATES = 0

_D = BinarySensorDeviceClass

BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    *(
        BinarySensorEntityDescription(key=key, device_class=_D.DOOR)
        for key in (
            "door_front_left",
            "door_front_right",
            "door_rear_left",
            "door_rear_right",
            "tailgate",
            "bonnet",
        )
    ),
    *(
        BinarySensorEntityDescription(key=key, device_class=_D.WINDOW)
        for key in (
            "window_front_left",
            "window_front_right",
            "window_rear_left",
            "window_rear_right",
            "sunroof",
        )
    ),
    # Aggregate "a door is open" from the live ReadModel (stsDoor).
    BinarySensorEntityDescription(key="door_open", device_class=_D.DOOR),
    # On means unlocked, per Home Assistant's lock device class.
    BinarySensorEntityDescription(key="unlocked", device_class=_D.LOCK),
    BinarySensorEntityDescription(key="ignition", device_class=_D.POWER),
    BinarySensorEntityDescription(key="climate", device_class=_D.RUNNING),
    BinarySensorEntityDescription(key="headlights", device_class=_D.LIGHT),
    BinarySensorEntityDescription(key="hazard", device_class=_D.LIGHT),
    BinarySensorEntityDescription(key="parklamp", device_class=_D.LIGHT),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdrenoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AdrenoxBinarySensor(coordinator, vin, d)
        for vin, status in coordinator.data.status.items()
        for d in BINARY_SENSORS
        if status.get(d.key) is not None
    )


class AdrenoxBinarySensor(AdrenoxEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: AdrenoxCoordinator,
        vin: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, vin, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        status = self.status
        return status.get(self.entity_description.key) if status else None
