"""Mahindra AdrenoX connected-car integration (unofficial)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AdrenoxApi
from .const import CONF_TOKENS
from .coordinator import AdrenoxConfigEntry, AdrenoxCoordinator
from .parser import Tokens

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.LOCK,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: AdrenoxConfigEntry) -> bool:
    stored = entry.data.get(CONF_TOKENS)
    if not stored:
        raise ConfigEntryAuthFailed("No AdrenoX session stored; sign in again")

    def save_tokens(tokens: Tokens) -> None:
        # Keep refreshed tokens so a restart does not need a new OTP.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKENS: tokens.as_dict()}
        )

    api = AdrenoxApi(async_get_clientsession(hass), Tokens.from_dict(stored), on_tokens=save_tokens)
    coordinator = AdrenoxCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_live()
    entry.runtime_data = coordinator

    # Reload when options change, but not when a token refresh rewrites data.
    options = dict(entry.options)

    async def _options_changed(hass: HomeAssistant, entry: AdrenoxConfigEntry) -> None:
        if dict(entry.options) != options:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_options_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AdrenoxConfigEntry) -> bool:
    await entry.runtime_data.async_stop_live()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
