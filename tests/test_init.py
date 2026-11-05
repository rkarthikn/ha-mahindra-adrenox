"""Setup, entities, backoff, reauth and diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.mahindra_adrenox.const import (
    CONF_APP_UNIQUE_ID,
    CONF_MOBILE,
    CONF_TOKENS,
    DOMAIN,
)
from custom_components.mahindra_adrenox.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import BASE, load

VIN = "MA1TEST0000000001"
VEHICLES_URL = f"{BASE}/getMyVehicleInfo"
# The coordinator keys fetchStateVin off the vehicle's encrypted id, not the VIN.
STATUS_URL = f"{BASE}/location-services/fetchStateVin/AbC-dEf_123=="


def _entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct",
        data={
            CONF_MOBILE: "9876543210",
            CONF_APP_UNIQUE_ID: "app-id",
            CONF_TOKENS: {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        },
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with aioresponses() as mock:
        mock.get(VEHICLES_URL, payload=load("vehicles.json"))
        mock.get(STATUS_URL, payload=load("status.json"))
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_creates_reported_entities(hass: HomeAssistant, endpoints) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    ids = {
        e.unique_id: e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    # Odometer, fuel range, ignition and GPS come from the fetchStateVin poll.
    assert f"{VIN}_odometer" in ids
    assert f"{VIN}_range" in ids
    assert f"{VIN}_last_updated" in ids
    assert f"{VIN}_ignition" in ids
    # Not reported by the snapshot (no HV battery here) -> no entity.
    assert f"{VIN}_fuel_level" not in ids
    # Lamps/doors/roof are live-only; with live off they don't appear.
    assert f"{VIN}_sunroof" not in ids
    # Remote commands stay off by default.
    assert f"{VIN}_door_lock" not in ids

    assert hass.states.get(ids[f"{VIN}_odometer"]).state == "18234.0"
    assert hass.states.get(ids[f"{VIN}_range"]).state == "410.0"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_auth_failure_starts_reauth(hass: HomeAssistant, endpoints) -> None:
    entry = _entry(hass)
    with aioresponses() as mock:
        mock.get(VEHICLES_URL, status=401, repeat=True)
        mock.get(f"{BASE}/auth/refreshToken", status=401, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # One refresh attempt, and no password login by itself.
        assert len(mock.requests) == 2
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_failures_back_off(hass: HomeAssistant, endpoints) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(minutes=15)

    with aioresponses() as mock:
        mock.get(STATUS_URL, status=503, repeat=True)
        await coordinator.async_refresh()
        assert coordinator.update_interval == timedelta(minutes=30)
        await coordinator.async_refresh()
        assert coordinator.update_interval == timedelta(minutes=60)
        for _ in range(5):
            await coordinator.async_refresh()
        assert coordinator.update_interval == timedelta(hours=2)

    with aioresponses() as mock:
        mock.get(STATUS_URL, status=429, headers={"Retry-After": "10800"})
        await coordinator.async_refresh()
        assert coordinator.update_interval == timedelta(hours=3)

    with aioresponses() as mock:
        mock.get(STATUS_URL, payload=load("status.json"))
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.update_interval == timedelta(minutes=15)


async def test_diagnostics_are_redacted(hass: HomeAssistant, endpoints) -> None:
    entry = _entry(hass)
    await _setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = repr(diag)
    for secret in ("9876543210", "access", VIN, "ma1test", "KA01AB1234", "Test XUV"):
        assert secret.lower() not in text.lower(), secret
    vehicle = diag["vehicles"][0]
    assert vehicle["parsed"]["odometer"] == 18234.0
    assert vehicle["status_raw"]["odoMeter"] == 18234
