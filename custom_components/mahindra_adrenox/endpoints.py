"""Where the AdrenoX cloud lives and which calls this integration makes.

Values here were recovered by statically reverse-engineering the official
"AdrenoX Connect" Android app (``com.mahindra.adrenox`` 2.0.68) — see
``docs/reverse-engineering.md``. They have NOT been confirmed against live
traffic yet, so expect to adjust a field or two the first time you run it with
debug logging on (``docs/capture-guide.md`` explains how).

Login model (verified from the app):
  * ``GET auth/login`` with the credentials as request *headers*
    (``X-Auth-Username`` = mobile number, ``X-Auth-Password`` = password,
    plus ``AppLogin`` / ``AppUniqueId``). There is no OTP for normal sign-in.
  * The JWT and refresh token come back in RESPONSE headers
    (``jwttoken`` / ``refreshToken``), not in the JSON body.
  * ``GET auth/refreshToken`` with the refresh token in the ``refreshToken``
    header returns a fresh pair the same way.
  * Every authorized call sends the raw JWT in ``Authorization`` (no "Bearer ").
"""

from __future__ import annotations

from typing import Any, Final

# Main REST API (res/values/strings.xml -> base_url). No trailing slash; the
# client joins paths with a single "/".
API_BASE: Final[str | None] = "https://interface.mahindramobilitysolution.com/V1"

# Live/location host (base_url_location_services) — used by the SSE channel.
SERVICE_BASE: Final[str | None] = "https://service.mahindramobilitysolution.com"

# Each call is (HTTP method, path). Paths may contain "{vin}".
Call = tuple[str, str]

# Auth. Credentials and tokens travel in headers (see module docstring).
LOGIN: Final[Call | None] = ("GET", "auth/login")
TOKEN_REFRESH: Final[Call | None] = ("GET", "auth/refreshToken")

# New-device mapping: a fresh AppUniqueId must be verified once with an OTP.
#   login -> AE1500 + temp token in x-amzn-remapped-authorization
#   GET generateOtpAppIdMapping (Authorization: temp) -> OTP sent to the mobile
#   POST auth/validate/otp (Authorization: temp, OTP, AppUniqueId) -> device mapped
GENERATE_APPID_OTP: Final[Call | None] = ("GET", "requestaccess/generateOtpAppIdMapping")
VALIDATE_APPID_OTP: Final[Call | None] = ("POST", "auth/validate/otp")

# Data.
VEHICLES: Final[Call | None] = ("GET", "requestaccess/getMyVehicleInfo")
# vehicleStatus takes the VIN as a ?vin= query parameter (handled in api.py),
# returning { code, data: { odoMeter, dte, vSpd, timeStamp }, ... }.
STATUS: Final[Call | None] = ("GET", "vehicleservice/vehicleStatus")

# Request header names for the login/refresh/mapping calls.
HDR_USERNAME: Final = "X-Auth-Username"
HDR_PASSWORD: Final = "X-Auth-Password"
HDR_APP_LOGIN: Final = "AppLogin"
HDR_APP_UNIQUE_ID: Final = "AppUniqueId"
HDR_REFRESH_TOKEN: Final = "refreshToken"
HDR_OTP: Final = "OTP"
HDR_AUTHORIZATION: Final = "Authorization"

# Response header names carrying the tokens.
RESP_HDR_JWT: Final = "jwttoken"
RESP_HDR_REFRESH: Final = "refreshToken"
# On AE1500 the login response carries a temporary token used to request and
# validate the device-mapping OTP.
RESP_HDR_TEMP_AUTH: Final = "x-amzn-remapped-authorization"

# App/User mapping failure for an unregistered AppUniqueId (Constants.AE1500).
APPID_MAPPING_CODE: Final = "AE1500"

# Remote commands. Left None on purpose: the app sends these as command-and-ack
# over rvdservices/remote-services, and the exact bodies were not verified. A
# wrong guess could act on a real car, so keep them off until confirmed.
COMMANDS: Final[dict[str, Call | None]] = {
    "lock": None,
    "unlock": None,
    "climate_start": None,
    "climate_stop": None,
}

# How the access token is sent; "{token}" is replaced. Raw JWT, no prefix.
AUTH_HEADER: Final[tuple[str, str]] = ("Authorization", "{token}")

# Headers the app sends on every REST request. The app's Retrofit interface
# annotates each call with Content-Type: application/json and Source: MMCApp;
# the client's own User-Agent is OkHttp's default. (The SSE live channel is the
# only place the app sends User-Agent: Android.)
EXTRA_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "application/json",
    "Source": "MMCApp",
    "User-Agent": "okhttp/4.12.0",
}


def command_body(command: str, vin: str) -> dict[str, Any] | None:
    return {"vin": vin}


def login_ready() -> bool:
    """True once every call needed to sign in and read status is known."""
    return API_BASE is not None and None not in (LOGIN, VEHICLES, STATUS)


def command_ready(command: str) -> bool:
    return API_BASE is not None and COMMANDS.get(command) is not None
