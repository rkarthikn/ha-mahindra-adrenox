"""Numeric readings: fuel, range, odometer, tyres, battery, temperatures."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AdrenoxConfigEntry, AdrenoxCoordinator
from .entity import AdrenoxEntity

PARALLEL_UPDATES = 0


def _distance(key: str, state_class: SensorStateClass) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=state_class,
        suggested_display_precision=0,
    )


def _tyre(key: str) -> SensorEntityDescription:
    # The app shows psi; change here if a capture shows bar or kPa.
    return SensorEntityDescription(
        key=key,
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    )


def _temperature(key: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    )


SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="fuel_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _distance("range", SensorStateClass.MEASUREMENT),
    _distance("odometer", SensorStateClass.TOTAL_INCREASING),
    _distance("service_due_distance", SensorStateClass.MEASUREMENT),
    SensorEntityDescription(
        key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _tyre("tyre_pressure_front_left"),
    _tyre("tyre_pressure_front_right"),
    _tyre("tyre_pressure_rear_left"),
    _tyre("tyre_pressure_rear_right"),
    _temperature("cabin_temperature"),
    _temperature("outside_temperature"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdrenoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for vin, status in coordinator.data.status.items():
        # Only what this car actually reports.
        entities.extend(
            AdrenoxSensor(coordinator, vin, d) for d in SENSORS if status.get(d.key) is not None
        )
        entities.append(AdrenoxUpdatedSensor(coordinator, vin))
    async_add_entities(entities)


class AdrenoxSensor(AdrenoxEntity, SensorEntity):
    def __init__(
        self, coordinator: AdrenoxCoordinator, vin: str, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, vin, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        status = self.status
        return status.get(self.entity_description.key) if status else None


class AdrenoxUpdatedSensor(AdrenoxEntity, SensorEntity):
    """When the car last reported, as opposed to when we last polled."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AdrenoxCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, "last_updated")

    @property
    def native_value(self) -> datetime | None:
        status = self.status
        return status.updated_at if status else None
