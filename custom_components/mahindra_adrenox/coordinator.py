"""Polls AdrenoX for every vehicle on the account, backing off on failure."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AdrenoxApi, AdrenoxAuthError, AdrenoxError, AdrenoxRateLimited
from .const import (
    COMMAND_REFRESH_DELAY,
    CONF_SCAN_MINUTES,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    MAX_BACKOFF,
)
from .parser import Vehicle, VehicleStatus

_LOGGER = logging.getLogger(__name__)

type AdrenoxConfigEntry = ConfigEntry[AdrenoxCoordinator]


@dataclass
class AdrenoxData:
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    status: dict[str, VehicleStatus] = field(default_factory=dict)


class AdrenoxCoordinator(DataUpdateCoordinator[AdrenoxData]):
    """One coordinator per AdrenoX account."""

    config_entry: AdrenoxConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AdrenoxConfigEntry, api: AdrenoxApi) -> None:
        self.base_interval = timedelta(
            minutes=entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self.base_interval,
        )
        self.api = api
        self._failures = 0
        self._pending_refresh: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> AdrenoxData:
        try:
            data = await self._fetch()
        except AdrenoxAuthError as err:
            # Stops polling until the owner signs in again with a new OTP.
            raise ConfigEntryAuthFailed(str(err)) from err
        except AdrenoxRateLimited as err:
            self._back_off(err.retry_after)
            raise UpdateFailed(str(err)) from err
        except AdrenoxError as err:
            self._back_off(None)
            raise UpdateFailed(str(err)) from err
        if self._failures:
            _LOGGER.info("AdrenoX reachable again; polling every %s", self.base_interval)
        self._failures = 0
        self.update_interval = self.base_interval
        return data

    def _back_off(self, retry_after: float | None) -> None:
        self._failures += 1
        interval = min(self.base_interval * (2 ** min(self._failures, 6)), MAX_BACKOFF)
        if retry_after:
            interval = max(interval, timedelta(seconds=retry_after))
        self.update_interval = interval
        _LOGGER.debug("AdrenoX poll failed %s times; next try in %s", self._failures, interval)

    async def _fetch(self) -> AdrenoxData:
        data = AdrenoxData()
        # The vehicle list rarely changes; fetch it once per load.
        if self.data is not None and self.data.vehicles:
            data.vehicles = self.data.vehicles
        else:
            data.vehicles = {v.vin: v for v in await self.api.vehicles()}
        for vin in data.vehicles:
            data.status[vin] = await self.api.status(vin)
        return data

    def refresh_after_command(self) -> None:
        """The car takes a while to act and report back; poll once more later."""
        if self._pending_refresh is not None:
            self._pending_refresh()

        async def _refresh(_now: datetime) -> None:
            self._pending_refresh = None
            await self.async_request_refresh()

        self._pending_refresh = async_call_later(self.hass, COMMAND_REFRESH_DELAY, _refresh)

    async def async_shutdown(self) -> None:
        if self._pending_refresh is not None:
            self._pending_refresh()
            self._pending_refresh = None
        await super().async_shutdown()
