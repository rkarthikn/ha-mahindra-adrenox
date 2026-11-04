"""The HTTP client: login, refresh rules, error mapping, never re-logging in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.mahindra_adrenox import endpoints as ep
from custom_components.mahindra_adrenox.api import (
    AdrenoxApi,
    AdrenoxAuthError,
    AdrenoxCommandError,
    AdrenoxError,
    AdrenoxNotConfigured,
    AdrenoxRateLimited,
)
from custom_components.mahindra_adrenox.parser import Tokens

from .conftest import BASE, load

VIN = "MA1TEST0000000001"
STATUS_URL = f"{BASE}/vehicleStatus?vin={VIN}"
APP_ID = "test-app-id"
FRESH = Tokens("old-access", "old-refresh", datetime.now(UTC) + timedelta(hours=1))
STALE = Tokens("old-access", "old-refresh", datetime.now(UTC) - timedelta(minutes=1))
LOGIN_HEADERS = {"jwttoken": "test-access-token", "refreshToken": "test-refresh-token"}


@pytest.fixture
async def session(hass: HomeAssistant) -> aiohttp.ClientSession:
    return async_get_clientsession(hass)


def _requests(mock: aioresponses, method: str, url: str) -> list:
    return mock.requests.get((method, URL(url)), [])


async def test_not_configured_makes_no_requests(session, monkeypatch) -> None:
    # If a future API change blanks the endpoints, make no network calls.
    monkeypatch.setattr(ep, "API_BASE", None)
    monkeypatch.setattr(ep, "LOGIN", None)
    monkeypatch.setattr(ep, "STATUS", None)
    api = AdrenoxApi(session, FRESH)
    with aioresponses() as mock:
        with pytest.raises(AdrenoxNotConfigured):
            await api.login("9876543210", "pw", APP_ID)
        with pytest.raises(AdrenoxNotConfigured):
            await api.status(VIN)
        assert not mock.requests


async def test_login_captures_header_tokens(session, endpoints) -> None:
    saved: list[Tokens] = []
    api = AdrenoxApi(session, on_tokens=saved.append)
    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/login", headers=LOGIN_HEADERS, payload={"statusCode": 200})
        tokens = await api.login("9876543210", "secret", APP_ID)

        req = _requests(mock, "GET", f"{BASE}/auth/login")[0]
        headers = req.kwargs["headers"]
        assert headers["X-Auth-Username"] == "9876543210"
        assert headers["X-Auth-Password"] == "secret"
        assert headers["AppLogin"] == "W601App"
        assert headers["AppUniqueId"] == APP_ID
        assert headers["Source"] == "MMCApp"
        assert "Authorization" not in headers
    assert tokens.access_token == "test-access-token"
    assert tokens.refresh_token == "test-refresh-token"
    assert saved == [tokens]


async def test_login_rejected_is_auth_error(session, endpoints) -> None:
    api = AdrenoxApi(session)
    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/login", status=401, payload={"message": "Bad creds"})
        with pytest.raises(AdrenoxAuthError, match="Bad creds"):
            await api.login("9876543210", "wrong", APP_ID)


async def test_status_sends_raw_token(session, endpoints) -> None:
    api = AdrenoxApi(session, FRESH)
    with aioresponses() as mock:
        mock.get(f"{BASE}/getMyVehicleInfo", payload=load("vehicles.json"))
        mock.get(STATUS_URL, payload=load("status.json"))
        vehicles = await api.vehicles()
        status = await api.status(vehicles[0].vin)
        req = _requests(mock, "GET", STATUS_URL)[0]
        assert req.kwargs["headers"]["Authorization"] == "old-access"  # raw, no Bearer
    assert status.get("odometer") == 18234.6
    assert status.get("range") == 410.0


async def test_expired_token_is_refreshed_first(session, endpoints) -> None:
    saved: list[Tokens] = []
    api = AdrenoxApi(session, Tokens(**vars(STALE)), on_tokens=saved.append)
    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/refreshToken", headers={"jwttoken": "new-access"})
        mock.get(STATUS_URL, payload=load("status.json"))
        await api.status(VIN)
        refresh = _requests(mock, "GET", f"{BASE}/auth/refreshToken")[0]
        assert refresh.kwargs["headers"]["refreshToken"] == "old-refresh"
        req = _requests(mock, "GET", STATUS_URL)[0]
        assert req.kwargs["headers"]["Authorization"] == "new-access"
    # The refresh token is kept when the response does not rotate it.
    assert saved[0].access_token == "new-access"
    assert saved[0].refresh_token == "old-refresh"


async def test_401_refreshes_once_then_gives_up(session, endpoints) -> None:
    api = AdrenoxApi(session, Tokens(**vars(FRESH)))
    with aioresponses() as mock:
        mock.get(STATUS_URL, status=401, repeat=True)
        mock.get(f"{BASE}/auth/refreshToken", headers={"jwttoken": "new-access"}, repeat=True)
        with pytest.raises(AdrenoxAuthError):
            await api.status(VIN)
        assert len(_requests(mock, "GET", STATUS_URL)) == 2
        assert len(_requests(mock, "GET", f"{BASE}/auth/refreshToken")) == 1
        # Never a fresh password login.
        assert not _requests(mock, "GET", f"{BASE}/auth/login")


async def test_refresh_rejected_is_auth_error(session, endpoints) -> None:
    api = AdrenoxApi(session, Tokens(**vars(STALE)))
    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/refreshToken", status=401)
        with pytest.raises(AdrenoxAuthError):
            await api.status(VIN)
        assert not _requests(mock, "GET", STATUS_URL)


async def test_stale_token_without_refresh_endpoint(session, endpoints, monkeypatch) -> None:
    monkeypatch.setattr(ep, "TOKEN_REFRESH", None)
    api = AdrenoxApi(session, Tokens(**vars(STALE)))
    with aioresponses() as mock:
        with pytest.raises(AdrenoxAuthError):
            await api.status(VIN)
        assert not mock.requests


async def test_rate_limited(session, endpoints) -> None:
    api = AdrenoxApi(session, FRESH)
    with aioresponses() as mock:
        mock.get(STATUS_URL, status=429, headers={"Retry-After": "120"})
        with pytest.raises(AdrenoxRateLimited) as err:
            await api.status(VIN)
    assert err.value.retry_after == 120


async def test_server_and_network_errors(session, endpoints) -> None:
    api = AdrenoxApi(session, FRESH)
    with aioresponses() as mock:
        mock.get(STATUS_URL, status=503, payload={"message": "down"})
        with pytest.raises(AdrenoxError, match="503: down"):
            await api.status(VIN)
        mock.get(STATUS_URL, exception=aiohttp.ClientConnectionError())
        with pytest.raises(AdrenoxError, match="Cannot reach"):
            await api.status(VIN)


async def test_commands_unconfigured_by_default(session, endpoints) -> None:
    api = AdrenoxApi(session, FRESH)
    with pytest.raises(AdrenoxNotConfigured):
        await api.command(VIN, "lock")


async def test_command_when_configured(session, endpoints, monkeypatch) -> None:
    api = AdrenoxApi(session, FRESH)
    monkeypatch.setitem(ep.COMMANDS, "lock", ("POST", "remote/{vin}/lock"))
    with aioresponses() as mock:
        mock.post(f"{BASE}/remote/{VIN}/lock", payload={"status": "ACCEPTED"})
        await api.command(VIN, "lock")
        assert _requests(mock, "POST", f"{BASE}/remote/{VIN}/lock")[0].kwargs["json"] == {
            "vin": VIN
        }
        mock.post(f"{BASE}/remote/{VIN}/lock", status=400, payload={"message": "Ignition on"})
        with pytest.raises(AdrenoxCommandError, match="Ignition on"):
            await api.command(VIN, "lock")
