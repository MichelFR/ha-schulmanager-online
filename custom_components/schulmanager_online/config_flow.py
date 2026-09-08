"""Config flow for Schulmanager Online."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    SchulmanagerAuthError,
    SchulmanagerClient,
    SchulmanagerError,
    SchulmanagerMultipleAccounts,
    SchulmanagerTwoFactorRequired,
    SsoMethod,
    parse_user_device,
)
from .const import (
    CONF_INSTITUTION_ID,
    CONF_USER_DEVICE,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

CONF_QUERY = "query"
CONF_SCHOOL = "school"
CONF_METHOD = "method"
CONF_REDIRECT_URL = "redirect_url"

STEP_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class SchulmanagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup flow.

    Two ways in: straight to email and password, or pick the school first, which
    is what makes the school's single sign-on providers discoverable.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SchulmanagerOptionsFlow:
        """Return the options flow."""
        return SchulmanagerOptionsFlow()

    def __init__(self) -> None:
        """Start with no school chosen."""
        self._institution_id: int | None = None
        self._schools: list[dict[str, Any]] = []
        self._sso_methods: list[SsoMethod] = []
        self._sso_method: SsoMethod | None = None

    def _client(self, email: str = "", password: str = "") -> SchulmanagerClient:
        """Build a client bound to the chosen school."""
        return SchulmanagerClient(
            async_get_clientsession(self.hass),
            email,
            password,
            institution_id=self._institution_id,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the user wants to log in."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["credentials", "pick_school"],
        )

    # ----------------------------------------------------------------- school

    async def async_step_pick_school(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search the public school directory."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                self._schools = await self._client().async_find_schools(
                    user_input[CONF_QUERY]
                )
            except SchulmanagerError:
                errors["base"] = "cannot_connect"
            else:
                if self._schools:
                    return await self.async_step_select_school()
                errors["base"] = "no_schools_found"

        return self.async_show_form(
            step_id="pick_school",
            data_schema=vol.Schema({vol.Required(CONF_QUERY): str}),
            errors=errors,
        )

    async def async_step_select_school(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one of the schools found, then look for single sign-on."""
        if user_input is not None:
            self._institution_id = int(user_input[CONF_SCHOOL])
            try:
                self._sso_methods = await self._client().async_get_sso_methods(
                    self._institution_id
                )
            except SchulmanagerError:
                # Losing the probe only costs the single sign-on buttons.
                _LOGGER.debug("Could not probe single sign-on", exc_info=True)
                self._sso_methods = []

            if self._sso_methods:
                return await self.async_step_login_method()
            return await self.async_step_credentials()

        options = {
            str(school["id"]): (
                f"{school['name']} — {school.get('zipcode', '')} "
                f"{school.get('city', '')}".strip()
            )
            for school in self._schools
        }
        return self.async_show_form(
            step_id="select_school",
            data_schema=vol.Schema({vol.Required(CONF_SCHOOL): vol.In(options)}),
        )

    async def async_step_login_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer email and password alongside the school's providers."""
        if user_input is not None:
            choice = user_input[CONF_METHOD]
            if choice == CONF_PASSWORD:
                return await self.async_step_credentials()
            self._sso_method = self._sso_methods[int(choice)]
            return await self.async_step_sso()

        options = {CONF_PASSWORD: "Email and password"} | {
            str(index): method.label for index, method in enumerate(self._sso_methods)
        }
        return self.async_show_form(
            step_id="login_method",
            data_schema=vol.Schema({vol.Required(CONF_METHOD): vol.In(options)}),
        )

    # -------------------------------------------------------------------- sso

    async def async_step_sso(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hand the browser flow to the user and take back the user device.

        The password goes to the school's identity provider, so Home Assistant
        cannot drive this; the user signs in and pastes the URL they land on,
        which carries the long-lived user device.
        """
        if self._sso_method is None:
            # Nothing to sign in with; send the user back to the start.
            return await self.async_step_user()
        errors: dict[str, str] = {}

        if user_input is not None:
            client = self._client()
            try:
                device = parse_user_device(user_input[CONF_REDIRECT_URL])
                await client.async_login_with_user_device(device)
            except SchulmanagerAuthError:
                errors["base"] = "invalid_user_device"
            except SchulmanagerError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create(client, {})

        return self.async_show_form(
            step_id="sso",
            data_schema=vol.Schema({vol.Required(CONF_REDIRECT_URL): str}),
            description_placeholders={
                "url": self._sso_method.url,
                "provider": self._sso_method.label,
            },
            errors=errors,
        )

    # ------------------------------------------------------------ credentials

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in with email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = self._client(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            try:
                await client.async_login()
            except SchulmanagerMultipleAccounts:
                errors["base"] = "multiple_accounts"
            except SchulmanagerTwoFactorRequired:
                errors["base"] = "two_factor_unsupported"
            except SchulmanagerAuthError:
                errors["base"] = "invalid_auth"
            except SchulmanagerError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create(client, user_input)

        return self.async_show_form(
            step_id="credentials",
            data_schema=STEP_CREDENTIALS_SCHEMA,
            errors=errors,
        )

    async def _async_create(
        self, client: SchulmanagerClient, user_input: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Store the working credentials as a config entry."""
        user = client.user or {}
        unique_id = str(user.get("id") or user_input.get(CONF_EMAIL, "")).lower()
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        data: dict[str, Any] = dict(user_input)
        if client.institution_id is not None:
            data[CONF_INSTITUTION_ID] = client.institution_id
        # The device re-mints tokens without the password and is the only
        # credential a single sign-on account has.
        if client.user_device:
            data[CONF_USER_DEVICE] = client.user_device

        title = user.get("email") or user_input.get(CONF_EMAIL) or "Schulmanager"
        return self.async_create_entry(title=str(title), data=data)

    # ----------------------------------------------------------------- reauth

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after the credentials were rejected."""
        self._institution_id = entry_data.get(CONF_INSTITUTION_ID)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            client = self._client(
                entry.data.get(CONF_EMAIL, ""), user_input[CONF_PASSWORD]
            )
            try:
                await client.async_login()
            except SchulmanagerAuthError:
                errors["base"] = "invalid_auth"
            except SchulmanagerError:
                errors["base"] = "cannot_connect"
            else:
                data = {**entry.data, **user_input}
                if client.user_device:
                    data[CONF_USER_DEVICE] = client.user_device
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


class SchulmanagerOptionsFlow(OptionsFlow):
    """Let the user trade freshness against politeness to the API."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the poll interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=MAX_SCAN_INTERVAL_MINUTES,
                        ),
                    )
                }
            ),
        )
