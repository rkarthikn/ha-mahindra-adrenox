"""Diagnostics: the raw payloads (so field names can be mapped), redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_MOBILE, CONF_PASSWORD, CONF_TOKENS
from .coordinator import AdrenoxConfigEntry

# Anything that identifies the owner or the car, or could be replayed.
TO_REDACT = {
    CONF_MOBILE,
    CONF_PASSWORD,
    CONF_TOKENS,
    "access_token",
    "refresh_token",
    "accessToken",
    "refreshToken",
    "jwttoken",
    "idToken",
    "token",
    "jwt",
    "vin",
    "VIN",
    "vinNumber",
    "vehicleVin",
    "chassisNumber",
    "engineNumber",
    "imei",
    "iccid",
    "msisdn",
    "registrationNumber",
    "regNo",
    "registrationNo",
    "vehicleNumber",
    "mobileNumber",
    "mobile",
    "phone",
    "email",
    "name",
    "firstName",
    "lastName",
    "customerName",
    "vehicleName",
    "nickName",
    "nickname",
    "address",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "lon",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AdrenoxConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    vehicles = []
    # Number the cars instead of showing VINs.
    for index, (vin, vehicle) in enumerate(coordinator.data.vehicles.items(), start=1):
        status = coordinator.data.status.get(vin)
        vehicles.append(
            {
                "vehicle": f"vehicle_{index}",
                "model": vehicle.model,
                "vehicle_raw": async_redact_data(vehicle.raw, TO_REDACT),
                "parsed": {
                    key: value
                    for key, value in (status.values.items() if status else ())
                    if value is not None
                },
                "has_position": bool(status and status.position),
                "updated_at": status.updated_at.isoformat()
                if status and status.updated_at
                else None,
                "status_raw": async_redact_data(status.raw, TO_REDACT) if status else None,
            }
        )
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "update_interval": str(coordinator.update_interval),
        "last_update_success": coordinator.last_update_success,
        "vehicles": vehicles,
    }
