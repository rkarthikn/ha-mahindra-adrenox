"""Config flow: registered mobile number + password (as the AdrenoX app)."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import endpoints as ep
from .api import (
    AdrenoxApi,
    AdrenoxAuthError,
    AdrenoxError,
    AdrenoxRateLimited,
    normalize_mobile,
)
from .const import (
    CONF_APP_UNIQUE_ID,
    CONF_MOBILE,
    CONF_PASSWORD,
    CONF_REMOTE_COMMANDS,
    CONF_SCAN_MINUTES,
    CONF_TOKENS,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    MAX_SCAN_MINUTES,
    MIN_SCAN_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MOBILE): TextSelector(TextSelectorConfig(type=TextSelectorType.TEL)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


def _account_id(mobile: str) -> str:
    # Unique id without keeping the phone number readable in the registry.
    return hashlib.sha256(f"adrenox:{mobile}".encode()).hexdigest()[:16]


class AdrenoxConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AdrenoxOptionsFlow:
        return AdrenoxOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not ep.login_ready():
            return self.async_abort(reason="endpoints_not_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mobile = normalize_mobile(user_input[CONF_MOBILE])
            except ValueError:
                errors[CONF_MOBILE] = "invalid_mobile"
            else:
                await self.async_set_unique_id(_account_id(mobile))
                self._abort_if_unique_id_configured()
                result = await self._try_login(mobile, user_input[CONF_PASSWORD])
                if isinstance(result, dict):
                    errors = result
                else:
                    tokens, app_unique_id, vehicles = result
                    if not vehicles:
                        return self.async_abort(reason="no_vehicles")
                    first = vehicles[0]
                    title = first.name or first.registration or first.model or "Mahindra"
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_MOBILE: mobile,
                            CONF_APP_UNIQUE_ID: app_unique_id,
                            CONF_TOKENS: tokens.as_dict(),
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def _try_login(self, mobile: str, password: str):
        """Return (tokens, app_unique_id, vehicles) on success, else errors dict."""
        app_unique_id = str(uuid.uuid4())
        api = AdrenoxApi(async_get_clientsession(self.hass))
        try:
            tokens = await api.login(mobile, password, app_unique_id)
            vehicles = await api.vehicles()
        except AdrenoxRateLimited:
            return {"base": "rate_limited"}
        except AdrenoxAuthError as err:
            # Logged at WARNING so the exact AdrenoX reason is visible without
            # enabling debug (e.g. wrong password vs. gateway rejection).
            _LOGGER.warning("AdrenoX rejected sign-in: %s", err)
            return {"base": "invalid_auth"}
        except AdrenoxError as err:
            _LOGGER.debug("AdrenoX sign-in failed: %s", err)
            return {"base": "cannot_connect"}
        return tokens, app_unique_id, vehicles

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        mobile = entry.data.get(CONF_MOBILE)
        errors: dict[str, str] = {}
        if user_input is not None and mobile:
            result = await self._try_login(mobile, user_input[CONF_PASSWORD])
            if isinstance(result, dict):
                errors = result
            else:
                tokens, app_unique_id, _ = result
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_APP_UNIQUE_ID: entry.data.get(CONF_APP_UNIQUE_ID) or app_unique_id,
                        CONF_TOKENS: tokens.as_dict(),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"mobile": f"******{(mobile or '')[-4:]}"},
        )


class AdrenoxOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_SCAN_MINUTES] = int(user_input[CONF_SCAN_MINUTES])
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_MINUTES, default=DEFAULT_SCAN_MINUTES): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_MINUTES,
                        max=MAX_SCAN_MINUTES,
                        step=5,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_REMOTE_COMMANDS, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
