"""Turns AdrenoX JSON into plain dataclasses (no Home Assistant imports).

The field paths below are guesses until checked against a real capture (see
``docs/capture-guide.md``). Each field lists candidate dotted paths, tried in
order; the first one present wins. Update the lists, not the entities, when
the real payload is known: a field whose paths never match simply produces no
entity.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
import json
from typing import Any

# Wrappers some APIs put the useful object in.
_ENVELOPES = ("data", "result", "response", "payload")


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None

    def expired(self, margin: timedelta, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at - margin

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tokens:
        expires = data.get("expires_at")
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.fromisoformat(expires) if expires else None,
        )


@dataclass
class Vehicle:
    vin: str
    name: str | None = None
    model: str | None = None
    registration: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    latitude: float
    longitude: float
    heading: float | None = None
    speed: float | None = None


@dataclass
class VehicleStatus:
    """Everything read from one status call. ``None`` means "not reported"."""

    values: dict[str, Any] = field(default_factory=dict)
    position: Position | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        return self.values.get(key)


# -- field maps ---------------------------------------------------------------

TOKEN_PATHS: dict[str, tuple[str, ...]] = {
    "access_token": ("access_token", "accessToken", "token", "idToken", "jwt"),
    "refresh_token": ("refresh_token", "refreshToken"),
    "expires_in": ("expires_in", "expiresIn", "expiry"),
}

VEHICLE_LIST_PATHS: tuple[str, ...] = ("vehicles", "vehicleList", "vehicleDetails", "")
VEHICLE_PATHS: dict[str, tuple[str, ...]] = {
    "vin": ("vin", "VIN", "vinNumber", "vehicleVin", "chassisNumber"),
    "name": ("nickName", "nickname", "vehicleName", "name"),
    "model": ("model", "modelName", "vehicleModel", "variant"),
    "registration": ("registrationNumber", "regNo", "registrationNo", "vehicleNumber"),
}

# Numeric readings.
NUMBER_PATHS: dict[str, tuple[str, ...]] = {
    "fuel_level": ("fuelLevel", "fuel.level", "fuelPercentage", "fuel_level"),
    # ICE: "fuel" is reported as distance-to-empty. "dte" is the verified key
    # from vehicleStatus; vehRange is the live-channel equivalent.
    "range": ("dte", "distanceToEmpty", "range", "vehRange", "fuel.range", "fuelRange"),
    "odometer": ("odoMeter", "odometer", "odo", "odometerReading", "totalDistance"),
    "battery_voltage": ("batteryVoltage", "battery.voltage", "batVoltage", "auxBatteryVoltage"),
    "tyre_pressure_front_left": (
        "tyrePressure.frontLeft",
        "tpms.fl",
        "tyrePressureFL",
        "tirePressure.frontLeft",
    ),
    "tyre_pressure_front_right": (
        "tyrePressure.frontRight",
        "tpms.fr",
        "tyrePressureFR",
        "tirePressure.frontRight",
    ),
    "tyre_pressure_rear_left": (
        "tyrePressure.rearLeft",
        "tpms.rl",
        "tyrePressureRL",
        "tirePressure.rearLeft",
    ),
    "tyre_pressure_rear_right": (
        "tyrePressure.rearRight",
        "tpms.rr",
        "tyrePressureRR",
        "tirePressure.rearRight",
    ),
    "cabin_temperature": ("cabinTemperature", "insideTemperature", "climate.cabinTemp"),
    "outside_temperature": ("outsideTemperature", "ambientTemperature", "climate.outsideTemp"),
    "service_due_distance": ("serviceDueKm", "nextServiceDistance", "service.dueKm"),
}

# On/off states. Each value is true when the thing is open/on/unlocked
# (Home Assistant's convention for door, window, lock and power sensors).
BOOL_PATHS: dict[str, tuple[str, ...]] = {
    "door_front_left": ("doors.frontLeft", "doorStatus.fl", "doorFL", "door.driver"),
    "door_front_right": ("doors.frontRight", "doorStatus.fr", "doorFR", "door.passenger"),
    "door_rear_left": ("doors.rearLeft", "doorStatus.rl", "doorRL"),
    "door_rear_right": ("doors.rearRight", "doorStatus.rr", "doorRR"),
    "tailgate": ("doors.tailgate", "doorStatus.tailgate", "tailgate", "boot", "trunk"),
    "bonnet": ("doors.bonnet", "doorStatus.bonnet", "bonnet", "hood"),
    # Aggregate "any door open" from the live ReadModel (stsDoor / stsDoorLatch).
    "door_open": ("stsDoor", "stsDoorLatch", "doorOpen", "anyDoorOpen"),
    "window_front_left": ("windows.frontLeft", "windowStatus.fl", "windowFL"),
    "window_front_right": ("windows.frontRight", "windowStatus.fr", "windowFR"),
    "window_rear_left": ("windows.rearLeft", "windowStatus.rl", "windowRL"),
    "window_rear_right": ("windows.rearRight", "windowStatus.rr", "windowRR"),
    "sunroof": ("sunRoof", "windows.sunroof", "sunroof", "sunroofStatus"),
    # doorLock is true when LOCKED, so it is inverted below.
    "unlocked": ("lockStatus", "doorLockStatus", "doorLock", "locked", "isLocked"),
    "ignition": ("ign", "ignition", "ignitionStatus", "engineStatus", "isIgnitionOn"),
    "climate": ("acStatus", "climate.status", "climateOn", "isAcOn"),
    # Headlamp on = either beam on, or the app's headLmpReq flag.
    "headlights": (
        "lampStatus.lowBeam",
        "lampStatus.highBeam",
        "headLmpReq",
        "headlights",
        "lights.head",
        "headlampStatus",
    ),
    "hazard": ("lampStatus.hazardLmp", "hazardLmp"),
    "parklamp": ("lampStatus.stsParkLmp", "lampStatus.stsPrkLmp", "stsParkLmp", "stsPrkLmp"),
}

# Keys whose raw value means the opposite of the HA state (true = locked).
# Only these matched paths mean "true == locked"; string statuses like
# lockStatus="UNLOCKED" already read correctly via _TRUE/_FALSE.
_INVERTED = {"unlocked": ("locked", "isLocked", "doorLock")}

POSITION_PATHS: dict[str, tuple[str, ...]] = {
    "latitude": ("location.latitude", "location.lat", "gps.lat", "latitude", "lat"),
    "longitude": (
        "location.longitude",
        "location.lng",
        "location.lon",
        "gps.lng",
        "gps.lon",
        "longitude",
        "lng",
        "lon",
    ),
    "heading": ("location.heading", "gps.heading", "heading", "direction"),
    "speed": ("location.speed", "gps.speed", "speed", "vSpd", "vehicleSpeed"),
}

UPDATED_PATHS: tuple[str, ...] = (
    "timeStamp",
    "lastUpdated",
    "lastUpdatedTime",
    "timestamp",
    "updatedAt",
    "eventTime",
    "location.timestamp",
)

# Timestamps without a zone from an Indian service are taken as IST.
_IST = timezone(timedelta(hours=5, minutes=30), "IST")

_TRUE = {
    "1",
    "true",
    "yes",
    "on",
    "open",
    "opened",
    "ajar",
    "unlocked",
    "unlock",
    "running",
    "active",
}
_FALSE = {
    "0",
    "false",
    "no",
    "off",
    "closed",
    "close",
    "shut",
    "locked",
    "lock",
    "stopped",
    "inactive",
}


# -- helpers --------------------------------------------------------------------


def unwrap(body: Any) -> Any:
    """Strip one or two ``{"data": ...}``-style envelopes."""
    for _ in range(2):
        if isinstance(body, dict):
            for key in _ENVELOPES:
                if key in body and isinstance(body[key], dict | list):
                    body = body[key]
                    break
            else:
                return body
    return body


def dig(data: Any, path: str) -> Any:
    """Read a dotted path; ``""`` returns ``data`` itself."""
    if path == "":
        return data
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def first(data: Any, paths: tuple[str, ...]) -> tuple[str | None, Any]:
    for path in paths:
        value = dig(data, path)
        if value is not None and value != "":
            return path, value
    return None, None


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        # Tolerate units glued on, e.g. "32 psi" or "12.4V".
        num = ""
        for ch in text:
            if ch.isdigit() or ch in ".-" or (ch == "+" and not num):
                num += ch
            elif num:
                break
        try:
            return float(num)
        except ValueError:
            return None
    return None


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def to_datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value  # epoch ms or s
        return datetime.fromtimestamp(seconds, UTC)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return to_datetime(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_IST)
        return parsed
    return None


def jwt_expiry(token: str) -> datetime | None:
    """Read ``exp`` from a JWT without verifying it (only to time refreshes)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    return to_datetime(exp) if isinstance(exp, int | float) else None


# -- parsers --------------------------------------------------------------------


class ParseError(ValueError):
    """The response did not contain what we need."""


def parse_tokens_from_headers(
    headers: Any,
    jwt_name: str = "jwttoken",
    refresh_name: str = "refreshToken",
    previous_refresh: str | None = None,
) -> Tokens:
    """Build Tokens from AdrenoX response headers.

    The app receives the JWT and refresh token in response *headers*, not the
    body. Expiry is read from the JWT's own ``exp`` claim. If a refresh call
    omits the refresh header, the previous one is kept.
    """
    access = headers.get(jwt_name) if headers is not None else None
    if not isinstance(access, str) or not access:
        raise ParseError(f"No '{jwt_name}' header in auth response")
    refresh = headers.get(refresh_name) if headers is not None else None
    if not (isinstance(refresh, str) and refresh):
        refresh = previous_refresh
    return Tokens(
        access_token=access,
        refresh_token=refresh,
        expires_at=jwt_expiry(access),
    )


def parse_tokens(body: Any, now: datetime | None = None) -> Tokens:
    data = unwrap(body)
    _, access = first(data, TOKEN_PATHS["access_token"])
    if not isinstance(access, str) or not access:
        raise ParseError("No access token in login response")
    _, refresh = first(data, TOKEN_PATHS["refresh_token"])
    _, expires_in = first(data, TOKEN_PATHS["expires_in"])
    expires_at = None
    seconds = to_float(expires_in)
    if seconds:
        expires_at = (now or datetime.now(UTC)) + timedelta(seconds=seconds)
    else:
        expires_at = jwt_expiry(access)
    return Tokens(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at=expires_at,
    )


def parse_vehicles(body: Any) -> list[Vehicle]:
    data = unwrap(body)
    items: Any = None
    for path in VEHICLE_LIST_PATHS:
        candidate = dig(data, path)
        if isinstance(candidate, list):
            items = candidate
            break
    if items is None and isinstance(data, dict):
        items = [data]  # a single-vehicle account may return one object
    vehicles: list[Vehicle] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        _, vin = first(item, VEHICLE_PATHS["vin"])
        if not vin:
            continue
        values = {k: first(item, p)[1] for k, p in VEHICLE_PATHS.items() if k != "vin"}
        vehicles.append(
            Vehicle(
                vin=str(vin).strip().upper(),
                name=_str(values["name"]),
                model=_str(values["model"]),
                registration=_str(values["registration"]),
                raw=item,
            )
        )
    return vehicles


def parse_status(body: Any) -> VehicleStatus:
    data = unwrap(body)
    if not isinstance(data, dict):
        raise ParseError("Status response is not an object")
    values: dict[str, Any] = {}
    for key, paths in NUMBER_PATHS.items():
        values[key] = to_float(first(data, paths)[1])
    for key, paths in BOOL_PATHS.items():
        path, raw = first(data, paths)
        state = to_bool(raw)
        if state is not None and path in _INVERTED.get(key, ()):
            state = not state
        values[key] = state

    position = None
    lat = to_float(first(data, POSITION_PATHS["latitude"])[1])
    lon = to_float(first(data, POSITION_PATHS["longitude"])[1])
    # 0,0 is what a modem without a fix often reports.
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        position = Position(
            latitude=lat,
            longitude=lon,
            heading=to_float(first(data, POSITION_PATHS["heading"])[1]),
            speed=to_float(first(data, POSITION_PATHS["speed"])[1]),
        )

    return VehicleStatus(
        values=values,
        position=position,
        updated_at=to_datetime(first(data, UPDATED_PATHS)[1]),
        raw=data,
    )


def _str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip()
