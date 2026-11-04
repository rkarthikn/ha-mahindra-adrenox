"""Constants for the Mahindra AdrenoX integration (no Home Assistant imports)."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mahindra_adrenox"
MANUFACTURER: Final = "Mahindra"

CONF_MOBILE: Final = "mobile"
CONF_PASSWORD: Final = "password"
CONF_TOKENS: Final = "tokens"
CONF_APP_UNIQUE_ID: Final = "app_unique_id"
CONF_SCAN_MINUTES: Final = "scan_minutes"
CONF_REMOTE_COMMANDS: Final = "remote_commands"

# The car's modem reports every few minutes at best, and each poll may wake
# Mahindra's backend; there is nothing to gain from polling faster.
DEFAULT_SCAN_MINUTES: Final = 15
MIN_SCAN_MINUTES: Final = 10
MAX_SCAN_MINUTES: Final = 120

# After a failed poll the interval doubles, up to this ceiling, and resets on
# the next success. Nothing here ever signs in again by itself.
MAX_BACKOFF: Final = timedelta(hours=2)

# Gap between any two requests from one account.
MIN_REQUEST_GAP: Final = 2.0
REQUEST_TIMEOUT: Final = 30

# Refresh the access token this long before it expires.
TOKEN_REFRESH_MARGIN: Final = timedelta(minutes=5)

# Poll again this long after a remote command, to show its result.
COMMAND_REFRESH_DELAY: Final = 45

# ---------------------------------------------------------------------------
# Values the official app sends so the backend sees a normal client. Recovered
# from com.mahindra.adrenox 2.0.68 (see docs/reverse-engineering.md).
# ---------------------------------------------------------------------------
# Util.getAppName() returns "W601App" for the standard app flavor.
APP_LOGIN_VALUE: Final = "W601App"
SOURCE_VALUE: Final = "MMCApp"
USER_AGENT: Final = "Android"
