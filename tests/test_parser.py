"""Parsing of auth headers, vehicle-list, status and live payloads."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json

import pytest

from custom_components.mahindra_adrenox.api import normalize_mobile
from custom_components.mahindra_adrenox.parser import (
    ParseError,
    Tokens,
    parse_status,
    parse_tokens,
    parse_tokens_from_headers,
    parse_vehicles,
    to_bool,
    to_datetime,
    to_float,
    unwrap,
)

from .conftest import load

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _jwt(claims: dict) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def test_tokens_from_headers() -> None:
    tokens = parse_tokens_from_headers(
        {"jwttoken": "test-access-token", "refreshToken": "test-refresh-token"}
    )
    assert tokens.access_token == "test-access-token"
    assert tokens.refresh_token == "test-refresh-token"


def test_tokens_from_headers_keeps_previous_refresh() -> None:
    tokens = parse_tokens_from_headers({"jwttoken": "new"}, previous_refresh="old-refresh")
    assert tokens.access_token == "new"
    assert tokens.refresh_token == "old-refresh"


def test_tokens_from_headers_missing_jwt() -> None:
    with pytest.raises(ParseError):
        parse_tokens_from_headers({"refreshToken": "r"})


def test_tokens_expiry_from_jwt() -> None:
    exp = int((NOW + timedelta(minutes=30)).timestamp())
    tokens = parse_tokens_from_headers({"jwttoken": _jwt({"exp": exp})})
    assert tokens.expires_at == NOW + timedelta(minutes=30)


def test_parse_tokens_body_still_supported() -> None:
    tokens = parse_tokens({"token": "abc", "refreshToken": "r"})
    assert tokens.access_token == "abc"
    assert tokens.refresh_token == "r"


def test_tokens_round_trip_and_expiry() -> None:
    tokens = Tokens("a", "r", NOW + timedelta(minutes=3))
    again = Tokens.from_dict(tokens.as_dict())
    assert again == tokens
    assert again.expired(timedelta(minutes=5), now=NOW)
    assert not again.expired(timedelta(minutes=1), now=NOW)
    assert not Tokens("a").expired(timedelta(minutes=5))


def test_vehicles() -> None:
    vehicles = parse_vehicles(load("vehicles.json"))
    assert len(vehicles) == 1
    car = vehicles[0]
    assert car.vin == "MA1TEST0000000001"
    assert car.name == "Test XUV"
    assert car.model == "XUV700 AX7"
    assert car.registration == "KA01AB1234"


def test_vehicles_single_object_and_bare_list() -> None:
    assert parse_vehicles({"data": {"vin": "X1"}})[0].vin == "X1"
    assert [v.vin for v in parse_vehicles([{"VIN": "a"}, {"vin": "b"}])] == ["A", "B"]
    assert parse_vehicles({"data": []}) == []


def test_status_passive() -> None:
    """vehicleStatus -> { data: { odoMeter, dte, vSpd, timeStamp } }."""
    status = parse_status(load("status.json"))
    assert status.get("odometer") == 18234.6
    assert status.get("range") == 410
    # No GPS in the passive status endpoint.
    assert status.position is None
    # timeStamp without a zone is read as IST.
    assert status.updated_at == datetime(2026, 11, 4, 4, 45, tzinfo=UTC)


def test_live_read_snapshot() -> None:
    """Live ReadModel: ignition, lamps, doors, roof."""
    status = parse_status(load("live_read.json"))
    assert status.get("ignition") is True
    assert status.get("odometer") == 18240
    assert status.get("range") == 405  # vehRange
    assert status.get("door_open") is True  # stsDoor OPEN
    assert status.get("unlocked") is True  # doorLock "0" -> unlocked
    assert status.get("sunroof") is False  # sunRoof CLOSED
    assert status.get("hazard") is True
    assert status.get("headlights") is True  # lowBeam on
    assert status.get("parklamp") is False


@pytest.mark.parametrize(
    ("payload", "unlocked"),
    [
        ({"lockStatus": "UNLOCKED"}, True),
        ({"lockStatus": "LOCKED"}, False),
        ({"locked": True}, False),
        ({"isLocked": False}, True),
        ({"doorLock": "1"}, False),
        ({"doorLock": "0"}, True),
    ],
)
def test_lock_polarity(payload: dict, unlocked: bool) -> None:
    assert parse_status(payload).get("unlocked") is unlocked


def test_status_without_fix_has_no_position() -> None:
    assert parse_status({"location": {"latitude": 0, "longitude": 0}}).position is None
    assert parse_status({"fuelLevel": 10}).position is None


def test_status_rejects_non_object() -> None:
    with pytest.raises(ParseError):
        parse_status({"data": [1, 2]})


def test_unwrap() -> None:
    assert unwrap({"data": {"result": {"a": 1}}}) == {"a": 1}
    assert unwrap({"data": "text", "a": 1}) == {"data": "text", "a": 1}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12, 12.0),
        ("12.6V", 12.6),
        ("1,234 km", 1234.0),
        ("-5", -5.0),
        ("n/a", None),
        (True, None),
        (None, None),
    ],
)
def test_to_float(value, expected) -> None:
    assert to_float(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("OPEN", True), ("Closed", False), (1, True), (0, False), ("weird", None), (None, None)],
)
def test_to_bool(value, expected) -> None:
    assert to_bool(value) is expected


def test_to_datetime() -> None:
    assert to_datetime(1_762_250_400) == datetime(2025, 11, 4, 10, 0, tzinfo=UTC)
    assert to_datetime(1_762_250_400_000) == datetime(2025, 11, 4, 10, 0, tzinfo=UTC)
    assert to_datetime("2026-11-04T10:00:00Z") == NOW
    assert to_datetime("yesterday") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("9876543210", "9876543210"),
        ("+91 98765 43210", "9876543210"),
        ("098765-43210", "9876543210"),
    ],
)
def test_normalize_mobile(value: str, expected: str) -> None:
    assert normalize_mobile(value) == expected


@pytest.mark.parametrize("value", ["12345", "5876543210", "+1 415 555 0100"])
def test_normalize_mobile_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_mobile(value)
