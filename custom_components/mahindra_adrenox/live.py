"""Optional live channel (Server-Sent Events) for ignition/lamps/doors/roof.

The app opens an SSE stream at
    {SERVICE_BASE}/location-services/webapi/sse/register/{vin}
with headers Authorization: <jwt>, Accept: text/event-stream, User-Agent:
Android (long read timeout). Events are JSON (LiveVehicleTrackEventData /
ReadModel) carrying ign, lampStatus, doorLock/stsDoor, sunRoof, odoMeter,
vehRange, speed, gnssInfo, etc. Those signals are NOT in the cheap vehicleStatus
poll, so this is the only way to get them.

It is OFF by default: holding a stream open 24/7 is less app-like than periodic
polling, and the richest data arrives only while the car is awake. Each raw
event is logged at DEBUG so field names can be confirmed, then parsed and merged
into the vehicle's status.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from datetime import UTC, datetime
import json
import logging
from typing import Any

import aiohttp

from . import endpoints as ep
from .api import AdrenoxApi
from .parser import live_timestamp, parse_live_values

_LOGGER = logging.getLogger(__name__)

_BACKOFF_START = 15
_BACKOFF_MAX = 300
_READ_TIMEOUT = 16 * 60  # the app uses a 15-minute read timeout


class AdrenoxLiveChannel:
    """Maintains the SSE connection for one vehicle and feeds parsed events."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api: AdrenoxApi,
        vin: str,
        on_event: Callable[[dict[str, Any], Any], None],
    ) -> None:
        self._session = session
        self._api = api
        self._vin = vin
        self._on_event = on_event
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"adrenox-sse-{self._vin}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def _url(self) -> str:
        return f"{ep.SERVICE_BASE}{ep.SSE_REGISTER_PATH}{self._vin}"

    async def _run(self) -> None:
        backoff = _BACKOFF_START
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = _BACKOFF_START
            except asyncio.CancelledError:
                raise
            except Exception as err:  # keep the loop alive on any failure
                _LOGGER.debug("AdrenoX live channel error: %s (retry in %ss)", err, backoff)
            if self._stop.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _connect_once(self) -> None:
        token = self._api.tokens.access_token if self._api.tokens else None
        if not token:
            raise RuntimeError("no access token for live channel")
        headers = {
            ep.HDR_AUTHORIZATION: token,
            "Accept": "text/event-stream",
            "User-Agent": "Android",
        }
        timeout = aiohttp.ClientTimeout(total=None, sock_read=_READ_TIMEOUT)
        async with self._session.get(self._url, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"SSE register HTTP {resp.status}")
            _LOGGER.debug("AdrenoX live channel open for %s", self._vin)
            data_lines: list[str] = []
            async for raw in resp.content:
                if self._stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        self._dispatch("\n".join(data_lines))
                        data_lines = []
                    continue
                if line.startswith(":"):  # keep-alive comment
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

    def _dispatch(self, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.debug("AdrenoX live: non-JSON payload: %s", payload[:120])
            return
        if not isinstance(event, dict):
            return
        _LOGGER.debug("AdrenoX live event for %s: %s", self._vin, event)
        values = parse_live_values(event)
        if not values:
            return
        updated = live_timestamp(event) or datetime.now(UTC)
        self._on_event(values, updated)
