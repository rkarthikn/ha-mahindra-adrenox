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
    CONF_ENABLE_LIVE,
    CONF_SCAN_MINUTES,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    MAX_BACKOFF,
)
from .live import AdrenoxLiveChannel
from .parser import Vehicle, VehicleStatus

# Keys that only ever arrive on the live SSE channel. Ignition is NOT here:
# the fetchStateVin snapshot already reports it (ign) on every poll.
LIVE_ONLY_KEYS = (
    "door_open",
    "unlocked",
    "hazard",
    "headlights",
    "parklamp",
    "sunroof",
)

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
        self.live_enabled = bool(entry.options.get(CONF_ENABLE_LIVE))
        self._live: list[AdrenoxLiveChannel] = []

    async def async_start_live(self) -> None:
        """Open the SSE channel per vehicle, if enabled in options."""
        if not self.live_enabled or self._live or not self.data:
            return
        session = self.api.session  # reuse the HA client session
        for vin, vehicle in self.data.vehicles.items():
            channel = AdrenoxLiveChannel(
                session,
                self.api,
                vin,
                lambda values, ts, v=vin: self._merge_live(v, values, ts),
                path_id=vehicle.encrypted_id,
            )
            channel.start()
            self._live.append(channel)

    async def async_stop_live(self) -> None:
        for channel in self._live:
            await channel.stop()
        self._live = []

    def _merge_live(self, vin: str, values: dict, updated) -> None:
        """Merge a live event into the vehicle's status and notify entities."""
        if not self.data:
            return
        status = self.data.status.get(vin)
        if status is None:
            return
        status.values.update(values)
        if updated is not None:
            status.updated_at = updated
        self.async_set_updated_data(self.data)

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
        for vin, vehicle in data.vehicles.items():
            # fetchStateVin keys off the vehicle's encrypted id (base64) in the
            # path, not the plain VIN. The account token alone authorizes it.
            status = await self.api.status(vin, path_id=vehicle.encrypted_id)
            # A poll only returns odo/fuel; don't let it wipe live-only values
            # (ignition, lamps, doors, roof) captured from the SSE channel.
            if self.live_enabled:
                prior = self.data.status.get(vin) if self.data else None
                for key in LIVE_ONLY_KEYS:
                    if prior is not None and prior.values.get(key) is not None:
                        status.values[key] = prior.values[key]
                    else:
                        status.values.setdefault(key, None)
            data.status[vin] = status
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
        await self.async_stop_live()
        await super().async_shutdown()
