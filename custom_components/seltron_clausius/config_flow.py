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

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
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
                CONF_PASSWORD: "",  # cleared after use
                CONF_ACCESS_TOKEN: self._tokens.access_token,
                CONF_REFRESH_TOKEN: self._tokens.refresh_token,
                CONF_EXPIRES_AT: self._tokens.expires_at,
                CONF_CONTROLLER_ID: installation.controller_id,
            },
        )

    # Reauth / reconfigure ostanejo enaki kot prej (skrajšano za berljivost)
    async def async_step_reauth(self, entry_data: dict[str, object]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        return await self._async_credentials(user_input, "reauth_confirm")

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        return await self._async_credentials(user_input, "reconfigure")

    async def _async_credentials(self, user_input, step_id: str) -> FlowResult:
        # ... (obstoječa logika, samo dodaj CONF_CONTROLLER_ID v update, če ga imaš)
        # Za zdaj lahko pustiš obstoječo implementacijo, ker reauth ne spreminja controllerja.
        errors: dict[str, str] = {}
        if user_input is not None:
            # ... (kopiraj obstoječo _async_credentials logiko)
            pass
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
    # ... ostane nespremenjen
    pass
