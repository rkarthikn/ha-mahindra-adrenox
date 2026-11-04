"""Async client for the AdrenoX cloud (no Home Assistant imports).

Login safety: this client never signs in by itself. After the first
password sign-in it keeps the session alive by refreshing the access token at
most once per request; if that fails it raises ``AdrenoxAuthError`` and Home
Assistant asks the owner to sign in again. It also spaces requests
``MIN_REQUEST_GAP`` seconds apart and stops on HTTP 429, so a stuck poll can
never hammer Mahindra's servers or look like brute force.

Protocol (reverse-engineered from the official app, see
``docs/reverse-engineering.md``):
  * ``GET auth/login`` with the mobile number and password as request headers;
    the JWT and refresh token come back in the RESPONSE headers.
  * The raw JWT (no "Bearer ") goes in ``Authorization`` on every call.
  * ``GET auth/refreshToken`` with the refresh token header renews both tokens.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import re
import time
from typing import Any

import aiohttp

from . import endpoints as ep
from .const import (
    APP_LOGIN_VALUE,
    MIN_REQUEST_GAP,
    REQUEST_TIMEOUT,
    TOKEN_REFRESH_MARGIN,
)
from .parser import (
    ParseError,
    Tokens,
    Vehicle,
    VehicleStatus,
    parse_status,
    parse_tokens_from_headers,
    parse_vehicles,
)

_LOGGER = logging.getLogger(__name__)


class AdrenoxError(Exception):
    """Generic API failure (network, server error, unexpected payload)."""


class AdrenoxNotConfigured(AdrenoxError):
    """The endpoint for this call has not been filled in yet."""


class AdrenoxAuthError(AdrenoxError):
    """Credentials were rejected; the owner must sign in again."""


class AdrenoxRateLimited(AdrenoxError):
    """The service asked us to slow down."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"Rate limited (retry after {retry_after or '?'} s)")
        self.retry_after = retry_after


class AdrenoxCommandError(AdrenoxError):
    """A remote command was refused."""


class AdrenoxDeviceMappingRequired(AdrenoxError):
    """A new AppUniqueId must be verified with an OTP before it can sign in.

    Carries the temporary token from the login response, used to request and
    validate the mapping OTP.
    """

    def __init__(self, temp_token: str | None) -> None:
        super().__init__("This device must be verified with an OTP")
        self.temp_token = temp_token


def normalize_mobile(value: str) -> str:
    """Return the 10-digit Indian mobile number, or raise ValueError."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("Not an Indian mobile number")
    return digits


@dataclass
class _Response:
    status: int
    body: Any
    headers: Any
    retry_after: str | None


TokenCallback = Callable[[Tokens], Awaitable[None] | None]


class AdrenoxApi:
    """One signed-in AdrenoX account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: Tokens | None = None,
        on_tokens: TokenCallback | None = None,
    ) -> None:
        self._session = session
        self.tokens = tokens
        self._on_tokens = on_tokens
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    # -- login ------------------------------------------------------------------

    async def login(self, mobile: str, password: str, app_unique_id: str) -> Tokens:
        """Sign in with mobile + password; tokens arrive in response headers."""
        if ep.API_BASE is None or ep.LOGIN is None:
            raise AdrenoxNotConfigured("AdrenoX endpoints are not configured yet")
        method, path = ep.LOGIN
        headers = {
            ep.HDR_USERNAME: mobile,
            ep.HDR_PASSWORD: password,
            ep.HDR_APP_LOGIN: APP_LOGIN_VALUE,
            ep.HDR_APP_UNIQUE_ID: app_unique_id,
        }
        resp = await self._send(method, self._url(path), None, extra_headers=headers)
        jwt = resp.headers.get(ep.RESP_HDR_JWT) if resp.headers is not None else None

        # Success is HTTP 200 carrying the jwttoken response header.
        if resp.status == 200 and jwt:
            tokens = parse_tokens_from_headers(resp.headers, ep.RESP_HDR_JWT, ep.RESP_HDR_REFRESH)
            await self._store(tokens)
            return tokens

        # A new/unmapped AppUniqueId: AE1500 "App and User Mapping Failed",
        # with a temporary token to drive the one-time OTP device verification.
        if _is_appid_mapping_error(resp.body):
            temp = resp.headers.get(ep.RESP_HDR_TEMP_AUTH) if resp.headers else None
            _LOGGER.debug(
                "AdrenoX wants device mapping (AE1500); temp token present=%s", bool(temp)
            )
            raise AdrenoxDeviceMappingRequired(temp)

        # Anything else is a failure. The app signals auth failures either as a
        # plain HTTP 401/403 (gateway) or as HTTP 200 with a JSON error body
        # like {"Code": "401", "message": "Authentication Failed: AE10131"}.
        detail = _auth_detail(resp.body)
        _LOGGER.debug(
            "AdrenoX login failed: HTTP %s, jwttoken header present=%s, body=%s",
            resp.status,
            bool(jwt),
            _safe_body(resp.body),
        )
        if resp.status == 429:
            raise AdrenoxRateLimited(_retry_after(resp.retry_after))
        if resp.status >= 500:
            raise AdrenoxError(f"AdrenoX server error (HTTP {resp.status})")
        raise AdrenoxAuthError(detail or f"login rejected (HTTP {resp.status})")

    # -- new-device OTP mapping -------------------------------------------------

    async def request_mapping_otp(self, temp_token: str) -> None:
        """Ask AdrenoX to SMS a device-mapping OTP (uses the temp token)."""
        if ep.API_BASE is None or ep.GENERATE_APPID_OTP is None:
            raise AdrenoxNotConfigured("AdrenoX endpoints are not configured yet")
        method, path = ep.GENERATE_APPID_OTP
        resp = await self._send(
            method, self._url(path), None, extra_headers={ep.HDR_AUTHORIZATION: temp_token}
        )
        if resp.status in (401, 403):
            raise AdrenoxAuthError("Device-mapping session expired; start again")
        self._raise_for_status(resp)

    async def validate_mapping_otp(
        self, temp_token: str, otp: str, app_unique_id: str, mobile: str, password: str
    ) -> Tokens:
        """Validate the OTP to map this AppUniqueId, then sign in normally."""
        if ep.API_BASE is None or ep.VALIDATE_APPID_OTP is None:
            raise AdrenoxNotConfigured("AdrenoX endpoints are not configured yet")
        method, path = ep.VALIDATE_APPID_OTP
        resp = await self._send(
            method,
            self._url(path),
            None,
            extra_headers={
                ep.HDR_AUTHORIZATION: temp_token,
                ep.HDR_OTP: otp,
                ep.HDR_APP_UNIQUE_ID: app_unique_id,
            },
        )
        if resp.status in (401, 403) or (resp.status == 200 and _auth_detail(resp.body)):
            raise AdrenoxAuthError(_auth_detail(resp.body) or "OTP was wrong or expired")
        self._raise_for_status(resp)
        # If validation already returned the real tokens, use them; otherwise the
        # device is now mapped, so a normal login succeeds.
        jwt = resp.headers.get(ep.RESP_HDR_JWT) if resp.headers else None
        if resp.status == 200 and jwt:
            tokens = parse_tokens_from_headers(resp.headers, ep.RESP_HDR_JWT, ep.RESP_HDR_REFRESH)
            await self._store(tokens)
            return tokens
        return await self.login(mobile, password, app_unique_id)

    # -- data -------------------------------------------------------------------

    async def vehicles(self) -> list[Vehicle]:
        resp = await self._authorized(ep.VEHICLES)
        return parse_vehicles(resp.body)

    async def switch_vehicle(self, encrypted_id: str) -> None:
        """Scope the session to one vehicle; a new JWT comes back in headers.

        vehicleStatus (and other per-vehicle calls) return UNAUTHORIZED with the
        account token until the session has been switched to that vehicle.
        """
        if ep.API_BASE is None:
            raise AdrenoxNotConfigured("AdrenoX endpoints are not configured yet")
        url = f"{ep.API_BASE}/auth/switch/vehicle/{encrypted_id}"
        async with self._lock:
            await self._ensure_token()
            resp = await self._send("GET", url, None, token=self._token())
            if resp.status in (401, 403) and self._can_refresh():
                await self._refresh()
                resp = await self._send("GET", url, None, token=self._token())
            self._raise_for_status(resp)
            jwt = resp.headers.get(ep.RESP_HDR_JWT) if resp.headers else None
            if jwt:
                tokens = parse_tokens_from_headers(
                    resp.headers,
                    ep.RESP_HDR_JWT,
                    ep.RESP_HDR_REFRESH,
                    previous_refresh=self.refresh_token,
                )
                await self._store(tokens)

    async def status(self, vin: str) -> VehicleStatus:
        resp = await self._authorized(ep.STATUS, params={"vin": vin})
        try:
            return parse_status(resp.body)
        except ParseError as err:
            raise AdrenoxError(str(err)) from err

    async def command(self, vin: str, command: str) -> None:
        call = ep.COMMANDS.get(command)
        if call is None:
            raise AdrenoxNotConfigured(f"Command {command} is not supported yet")
        try:
            await self._authorized(call, json=ep.command_body(command, vin), vin=vin)
        except (AdrenoxAuthError, AdrenoxNotConfigured):
            raise
        except AdrenoxError as err:
            raise AdrenoxCommandError(str(err)) from err

    # -- plumbing ---------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{ep.API_BASE}/{path.lstrip('/')}"

    async def _authorized(
        self,
        call: ep.Call | None,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        vin: str | None = None,
    ) -> _Response:
        if ep.API_BASE is None or call is None:
            raise AdrenoxNotConfigured("AdrenoX endpoints are not configured yet")
        method, path = call
        if vin is not None:
            path = path.replace("{vin}", vin)
        url = self._url(path)

        async with self._lock:
            await self._ensure_token()
            resp = await self._send(method, url, json, token=self._token(), params=params)
            if resp.status in (401, 403):
                # One refresh, one retry. Never a fresh password login.
                if not self._can_refresh():
                    raise AdrenoxAuthError(_detail(resp.body) or f"HTTP {resp.status}")
                await self._refresh()
                resp = await self._send(method, url, json, token=self._token(), params=params)
            self._raise_for_status(resp)
            return resp

    def _token(self) -> str:
        if self.tokens is None:
            raise AdrenoxAuthError("Not signed in")
        return self.tokens.access_token

    def _can_refresh(self) -> bool:
        return (
            ep.TOKEN_REFRESH is not None
            and self.tokens is not None
            and self.tokens.refresh_token is not None
        )

    async def _ensure_token(self) -> None:
        if self.tokens is None:
            raise AdrenoxAuthError("Not signed in")
        if self.tokens.expired(TOKEN_REFRESH_MARGIN):
            if not self._can_refresh():
                raise AdrenoxAuthError("Session expired")
            await self._refresh()

    async def _refresh(self) -> None:
        assert self.tokens is not None and self.tokens.refresh_token is not None
        assert ep.TOKEN_REFRESH is not None
        method, path = ep.TOKEN_REFRESH
        previous = self.tokens.refresh_token
        resp = await self._send(
            method, self._url(path), None, extra_headers={ep.HDR_REFRESH_TOKEN: previous}
        )
        if resp.status in (401, 403):
            raise AdrenoxAuthError(_detail(resp.body) or f"HTTP {resp.status}")
        self._raise_for_status(resp)
        try:
            tokens = parse_tokens_from_headers(
                resp.headers, ep.RESP_HDR_JWT, ep.RESP_HDR_REFRESH, previous_refresh=previous
            )
        except ParseError as err:
            raise AdrenoxAuthError(f"Token refresh failed: {err}") from err
        await self._store(tokens)

    async def _store(self, tokens: Tokens) -> None:
        self.tokens = tokens
        if self._on_tokens is not None:
            result = self._on_tokens(tokens)
            if result is not None:
                await result

    async def _send(
        self,
        method: str,
        url: str,
        json: dict[str, Any] | None,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> _Response:
        wait = MIN_REQUEST_GAP - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)

        headers = {"Accept": "application/json", **ep.EXTRA_HEADERS}
        if token is not None:
            name, template = ep.AUTH_HEADER
            headers[name] = template.format(token=token)
        if extra_headers:
            headers.update(extra_headers)

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                self._last_request = time.monotonic()
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
                return _Response(resp.status, body, resp.headers, resp.headers.get("Retry-After"))
        except (aiohttp.ClientError, TimeoutError) as err:
            self._last_request = time.monotonic()
            raise AdrenoxError(f"Cannot reach AdrenoX: {err}") from err

    def _raise_for_status(self, resp: _Response) -> None:
        if resp.status in (401, 403):
            raise AdrenoxAuthError(_detail(resp.body) or f"HTTP {resp.status}")
        if resp.status == 429:
            raise AdrenoxRateLimited(_retry_after(resp.retry_after))
        if resp.status >= 400:
            raise AdrenoxError(f"HTTP {resp.status}: {_detail(resp.body)}")


def _detail(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("message", "error_description", "error", "detail", "msg"):
            if isinstance(body.get(key), str):
                return body[key]
    return ""


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


# AdrenoX auth-failure messages seen in the app (ApiClient.logInApi).
_AUTH_MESSAGES = {
    "authentication failed: ae10131": "Mobile number or password is incorrect",
    "authentication failed: ae10132": "Mobile number or password is incorrect",
    "authentication failed: ae1013": "Account locked after too many attempts",
    "authentication failed: ae1014": "Account is deactivated",
    "authentication failed: ae1054": "Vehicle has been de-linked from this account",
}


def _is_appid_mapping_error(body: Any) -> bool:
    """True when login returned the AE1500 app/user mapping failure."""
    if not isinstance(body, dict):
        return False
    code = str(body.get("Code") or body.get("code") or "").strip().upper()
    if code == ep.APPID_MAPPING_CODE:
        return True
    msg = str(body.get("message") or body.get("Message") or "").lower()
    return "mapping failed" in msg


def _auth_detail(body: Any) -> str:
    """Human-readable reason from a login error body, if any."""
    if not isinstance(body, dict):
        return ""
    msg = body.get("message") or body.get("Message")
    code = body.get("Code") or body.get("code")
    if isinstance(msg, str):
        known = _AUTH_MESSAGES.get(msg.strip().lower())
        if known:
            return known
        if "multiple" in msg.lower() or "another device" in msg.lower():
            return "Already signed in on another device"
        return msg
    if code is not None:
        return f"Login failed (code {code})"
    return ""


def _safe_body(body: Any) -> str:
    """A short, non-secret preview of a response body for debug logs."""
    if isinstance(body, dict):
        return str({k: body[k] for k in list(body)[:8] if "token" not in k.lower()})
    return str(body)[:200]
