"""Config, reauth and options flows."""

from __future__ import annotations

from aioresponses import aioresponses
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mahindra_adrenox import endpoints as ep
from custom_components.mahindra_adrenox.config_flow import _account_id
from custom_components.mahindra_adrenox.const import (
    CONF_MOBILE,
    CONF_PASSWORD,
    CONF_REMOTE_COMMANDS,
    CONF_SCAN_MINUTES,
    CONF_TOKENS,
    DOMAIN,
)

from .conftest import BASE, load

MOBILE = "9876543210"
LOGIN_HEADERS = {"jwttoken": "test-access-token", "refreshToken": "test-refresh-token"}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_aborts_until_endpoints_are_mapped(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(ep, "API_BASE", None)
    with aioresponses() as mock:
        result = await _start(hass)
        assert not mock.requests
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "endpoints_not_configured"


async def test_full_flow(hass: HomeAssistant, endpoints) -> None:
    result = await _start(hass)
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "12", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {CONF_MOBILE: "invalid_mobile"}

    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/login", status=401)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MOBILE: "+91 98765 43210", CONF_PASSWORD: "wrong"}
        )
        assert result["errors"] == {"base": "invalid_auth"}

        mock.get(f"{BASE}/auth/login", headers=LOGIN_HEADERS, payload={"statusCode": 200})
        mock.get(f"{BASE}/getMyVehicleInfo", payload=load("vehicles.json"))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MOBILE: MOBILE, CONF_PASSWORD: "secret"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test XUV"
    assert result["data"][CONF_MOBILE] == MOBILE
    assert result["data"][CONF_TOKENS]["access_token"] == "test-access-token"
    assert CONF_PASSWORD not in result["data"]  # password is never stored
    assert result["result"].unique_id == _account_id(MOBILE)


async def test_new_device_otp_flow(hass: HomeAssistant, endpoints) -> None:
    result = await _start(hass)
    with aioresponses() as mock:
        # Login from a new device -> AE1500 + temp token, then OTP is sent.
        mock.get(
            f"{BASE}/auth/login",
            status=200,
            headers={"x-amzn-remapped-authorization": "temp-tok"},
            payload={"Code": "AE1500", "message": "App and User Mapping Failed"},
        )
        mock.get(f"{BASE}/requestaccess/generateOtpAppIdMapping", payload={"statusCode": 200})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MOBILE: MOBILE, CONF_PASSWORD: "secret"}
        )
        assert result["step_id"] == "otp"
        assert result["description_placeholders"] == {"mobile": "******3210"}

        # Wrong code first.
        mock.post(f"{BASE}/auth/validate/otp", status=401, payload={"message": "Invalid OTP"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"otp": "000000"}
        )
        assert result["errors"] == {"base": "invalid_otp"}

        # Correct code: device mapped, tokens in headers, vehicles read.
        mock.post(
            f"{BASE}/auth/validate/otp",
            status=200,
            headers={"jwttoken": "test-access-token", "refreshToken": "r"},
            payload={"statusCode": 200},
        )
        mock.get(f"{BASE}/getMyVehicleInfo", payload=load("vehicles.json"))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"otp": "123456"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test XUV"
    assert result["data"][CONF_TOKENS]["access_token"] == "test-access-token"


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, "rate_limited"), (403, "invalid_auth"), (500, "cannot_connect")],
)
async def test_login_errors(hass: HomeAssistant, endpoints, status, error) -> None:
    result = await _start(hass)
    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/login", status=status, payload={})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MOBILE: MOBILE, CONF_PASSWORD: "pw"}
        )
    assert result["errors"] == {"base": error}


async def test_duplicate_account(hass: HomeAssistant, endpoints) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=_account_id(MOBILE)).add_to_hass(hass)
    result = await _start(hass)
    with aioresponses() as mock:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MOBILE: MOBILE, CONF_PASSWORD: "pw"}
        )
        assert not mock.requests
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, endpoints) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_account_id(MOBILE),
        data={CONF_MOBILE: MOBILE, CONF_TOKENS: {"access_token": "dead"}},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with aioresponses() as mock:
        mock.get(f"{BASE}/auth/login", headers=LOGIN_HEADERS, payload={"statusCode": 200})
        mock.get(f"{BASE}/getMyVehicleInfo", payload=load("vehicles.json"))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKENS]["access_token"] == "test-access-token"


async def test_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_MINUTES: 30.0, CONF_REMOTE_COMMANDS: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_MINUTES: 30, CONF_REMOTE_COMMANDS: True}
