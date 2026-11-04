"""Config flow: mobile + password, with a one-time OTP to map a new device."""

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
    AdrenoxDeviceMappingRequired,
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
OTP_SCHEMA = vol.Schema({vol.Required("otp"): TextSelector(TextSelectorConfig())})


def _account_id(mobile: str) -> str:
    # Unique id without keeping the phone number readable in the registry.
    return hashlib.sha256(f"adrenox:{mobile}".encode()).hexdigest()[:16]


class AdrenoxConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._mobile: str | None = None
        self._password: str | None = None
        self._app_unique_id: str | None = None
        self._temp_token: str | None = None
        self._api: AdrenoxApi | None = None

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
                self._mobile = mobile
                self._password = user_input[CONF_PASSWORD]
                self._app_unique_id = str(uuid.uuid4())
                errors = await self._sign_in()
                if not errors:
                    if self._temp_token is not None:
                        return await self.async_step_otp()
                    return await self._create_entry()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_otp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._api is not None
            try:
                await self._api.validate_mapping_otp(
                    self._temp_token,
                    user_input["otp"].strip(),
                    self._app_unique_id,
                    self._mobile,
                    self._password,
                )
            except AdrenoxRateLimited:
                errors["base"] = "rate_limited"
            except AdrenoxAuthError:
                errors["base"] = "invalid_otp"
            except AdrenoxError as err:
                _LOGGER.debug("OTP validation failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._create_entry()

        return self.async_show_form(
            step_id="otp",
            data_schema=OTP_SCHEMA,
            errors=errors,
            description_placeholders={"mobile": f"******{(self._mobile or '')[-4:]}"},
        )

    async def _sign_in(self) -> dict[str, str]:
        """Try to sign in; set self._temp_token if a device OTP is needed."""
        self._temp_token = None
        self._api = AdrenoxApi(async_get_clientsession(self.hass))
        try:
            await self._api.login(self._mobile, self._password, self._app_unique_id)
        except AdrenoxDeviceMappingRequired as err:
            # New device: send the mapping OTP and continue to the OTP step.
            if not err.temp_token:
                _LOGGER.warning("AdrenoX asked for device mapping but sent no temp token")
                return {"base": "cannot_connect"}
            self._temp_token = err.temp_token
            try:
                await self._api.request_mapping_otp(err.temp_token)
            except AdrenoxRateLimited:
                return {"base": "rate_limited"}
            except AdrenoxError as otp_err:
                _LOGGER.debug("Could not send mapping OTP: %s", otp_err)
                return {"base": "cannot_connect"}
            return {}
        except AdrenoxRateLimited:
            return {"base": "rate_limited"}
        except AdrenoxAuthError as err:
            _LOGGER.warning("AdrenoX rejected sign-in: %s", err)
            return {"base": "invalid_auth"}
        except AdrenoxError as err:
            _LOGGER.debug("AdrenoX sign-in failed: %s", err)
            return {"base": "cannot_connect"}
        return {}

    async def _create_entry(self) -> ConfigFlowResult:
        assert self._api is not None
        try:
            vehicles = await self._api.vehicles()
        except AdrenoxError as err:
            _LOGGER.debug("Could not read vehicles after sign-in: %s", err)
            return self.async_abort(reason="cannot_connect")
        if not vehicles:
            return self.async_abort(reason="no_vehicles")

        data = {
            CONF_MOBILE: self._mobile,
            CONF_APP_UNIQUE_ID: self._app_unique_id,
            CONF_TOKENS: self._api.tokens.as_dict(),
        }
        if self.source == "reauth":
            entry = self._get_reauth_entry()
            return self.async_update_reload_and_abort(entry, data={**entry.data, **data})
        first = vehicles[0]
        title = first.name or first.registration or first.model or "Mahindra"
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        self._mobile = entry.data.get(CONF_MOBILE)
        # Reuse the already-mapped device id so no new OTP is needed.
        self._app_unique_id = entry.data.get(CONF_APP_UNIQUE_ID) or str(uuid.uuid4())
        errors: dict[str, str] = {}
        if user_input is not None and self._mobile:
            self._password = user_input[CONF_PASSWORD]
            errors = await self._sign_in()
            if not errors:
                if self._temp_token is not None:
                    return await self.async_step_otp()
                return await self._create_entry()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"mobile": f"******{(self._mobile or '')[-4:]}"},
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
