from __future__ import annotations

import hashlib

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, Installation, SeltronApi, async_password_login
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_POLLING_INTERVAL,
    CONF_REFRESH_TOKEN,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    POLLING_INTERVAL_OPTIONS,
    POLLING_INTERVALS,
    polling_interval_seconds,
)
from .labels import CONF_LABELS, normalize_labels

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_CONTROLLER_ID = "controller_id"


class SeltronConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure SeltronHome with local credentials for automatic recovery."""

    VERSION = 1

    def __init__(self) -> None:
        self._installations: list[Installation] = []
        self._tokens = None
        self._email = ""

    @staticmethod
    def async_get_options_flow(config_entry):
        return SeltronOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input[CONF_EMAIL]).strip().casefold()
            password = str(user_input[CONF_PASSWORD])
            session = async_get_clientsession(self.hass)
            try:
                tokens = await async_password_login(session, email, password)
                installations = await SeltronApi(
                    session, access_token=tokens.access_token
                ).async_discover_installations()
                if not installations:
                    errors["base"] = "no_installations"
                else:
                    self._tokens = tokens
                    self._email = email
                    self._installations = installations
                    if len(installations) == 1:
                        return await self._async_create_entry(installations[0])
                    return await self.async_step_select_controller()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            finally:
                password = ""

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_controller(self, user_input=None) -> FlowResult:
        """Let the user pick which controller to add."""
        if user_input is not None:
            controller_id = user_input[CONF_CONTROLLER_ID]
            installation = next(
                (i for i in self._installations if i.controller_id == controller_id),
                None,
            )
            if installation:
                return await self._async_create_entry(installation)

        options = {
            inst.controller_id: f"{inst.controller.get('model', 'WDC')} ({inst.controller_id[:8]}…)"
            for inst in self._installations
        }
        return self.async_show_form(
            step_id="select_controller",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONTROLLER_ID): vol.In(options)}
            ),
        )

    async def _async_create_entry(self, installation: Installation) -> FlowResult:
        """Create the config entry for the selected controller."""
        unique_id = hashlib.sha256(
            f"{self._email}:{installation.controller_id}".encode("utf-8")
        ).hexdigest()
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        model = installation.controller.get("model", "WDC")
        title = f"Seltron {model}"

        return self.async_create_entry(
            title=title,
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: "",
                CONF_ACCESS_TOKEN: self._tokens.access_token,
                CONF_REFRESH_TOKEN: self._tokens.refresh_token,
                CONF_EXPIRES_AT: self._tokens.expires_at,
                CONF_CONTROLLER_ID: installation.controller_id,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, object]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        return await self._async_credentials(user_input, "reauth_confirm")

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        return await self._async_credentials(user_input, "reconfigure")

    async def _async_credentials(self, user_input, step_id: str) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input[CONF_EMAIL]).strip().casefold()
            password = str(user_input[CONF_PASSWORD])
            entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
            if entry is None:
                return self.async_abort(reason="reauth_entry_missing")
            expected = hashlib.sha256(
                f"{email}:{entry.data.get(CONF_CONTROLLER_ID, '')}".encode("utf-8")
            ).hexdigest()
            if expected != entry.unique_id:
                errors["base"] = "wrong_account"
            else:
                session = async_get_clientsession(self.hass)
                try:
                    tokens = await async_password_login(session, email, password)
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_EMAIL: email,
                            CONF_PASSWORD: "",
                            CONF_ACCESS_TOKEN: tokens.access_token,
                            CONF_REFRESH_TOKEN: tokens.refresh_token,
                            CONF_EXPIRES_AT: tokens.expires_at,
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(
                        reason="reauth_successful"
                        if step_id == "reauth_confirm"
                        else "reconfigure_successful"
                    )
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except (
                    aiohttp.ClientError,
                    TimeoutError,
                    RuntimeError,
                    ValueError,
                    KeyError,
                ):
                    errors["base"] = "cannot_connect"
                finally:
                    password = ""

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )


class SeltronOptionsFlow(config_entries.OptionsFlow):
    """Configure local display labels for currently discovered stable channels."""

    def __init__(self, config_entry) -> None:
        self._seltron_entry = config_entry

    def _label_fields(self) -> list[tuple[str, str]]:
        coordinator = self.hass.data[DOMAIN][self._seltron_entry.entry_id]
        status = coordinator.data.status
        fields: list[tuple[str, str]] = []
        seen_temperatures: set[str] = set()
        for item in status.temperatures:
            if item.code not in seen_temperatures:
                fields.append((f"temperature:{item.code}", item.name))
                seen_temperatures.add(item.code)
        fields.extend((f"relay:{item.code}", item.name) for item in status.relays)
        fields.extend((f"circuit:{item.code}", item.name) for item in status.circuits)
        return fields

    async def async_step_init(self, user_input=None) -> FlowResult:
        fields = self._label_fields()
        allowed = {key for key, _fallback in fields}
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                polling_interval = user_input.get(CONF_POLLING_INTERVAL)
                if (
                    type(polling_interval) is not int
                    or polling_interval not in POLLING_INTERVALS
                ):
                    raise ValueError("Unknown polling interval")
                label_input = {
                    key: value
                    for key, value in user_input.items()
                    if key != CONF_POLLING_INTERVAL
                }
                if not set(label_input).issubset(allowed):
                    raise ValueError("Unknown label channel")
                return self.async_create_entry(
                    title="",
                    data={
                        **self._seltron_entry.options,
                        CONF_POLLING_INTERVAL: polling_interval,
                        CONF_LABELS: normalize_labels(label_input),
                    },
                )
            except ValueError as err:
                errors["base"] = (
                    "invalid_polling_interval"
                    if str(err) == "Unknown polling interval"
                    else "invalid_label"
                )

        existing = self._seltron_entry.options.get(CONF_LABELS, {})
        existing_polling_interval = polling_interval_seconds(
            self._seltron_entry.options.get(
                CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
            )
        )
        schema_fields = {
            vol.Required(
                CONF_POLLING_INTERVAL,
                default=existing_polling_interval,
            ): vol.In(POLLING_INTERVAL_OPTIONS)
        }
        schema_fields.update(
            {
                vol.Optional(
                    key,
                    default=existing.get(key, "") if isinstance(existing, dict) else "",
                    description={"suggested_value": fallback},
                ): str
                for key, fallback in fields
            }
        )
        schema = vol.Schema(schema_fields)
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
