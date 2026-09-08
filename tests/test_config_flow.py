"""Tests for the config flow, including the single sign-on path."""

from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import quote

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.schulmanager_online.api import (
    SchulmanagerAuthError,
    SsoMethod,
)
from custom_components.schulmanager_online.const import (
    CONF_INSTITUTION_ID,
    CONF_USER_DEVICE,
    DOMAIN,
)

USER = {"id": 42, "email": "user@example.com"}
SCHOOLS = [
    {
        "id": 1234,
        "name": "Musterschule Nord",
        "zipcode": "10115",
        "city": "Musterstadt",
    },
    {
        "id": 5678,
        "name": "Musterschule Süd",
        "zipcode": "10117",
        "city": "Musterstadt",
    },
]
DEVICE = {"id": 99, "key": "device-key"}


@pytest.fixture(autouse=True)
def no_setup():
    """Stop the flow from actually setting the entry up."""
    with patch(
        "custom_components.schulmanager_online.async_setup_entry", return_value=True
    ):
        yield


async def test_menu_offered(hass: HomeAssistant) -> None:
    """The first step should ask how to log in."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"credentials", "pick_school"}


async def test_password_login(hass: HomeAssistant) -> None:
    """Email and password should create an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "credentials"}
    )
    assert result["step_id"] == "credentials"

    async def fake_login(self, two_factor_code=None):
        self.user = USER

    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient.async_login",
        fake_login,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EMAIL] == "user@example.com"
    assert result["result"].unique_id == "42"


async def test_bad_password_shows_error(hass: HomeAssistant) -> None:
    """A rejected password should re-show the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "credentials"}
    )
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient.async_login",
        side_effect=SchulmanagerAuthError("nope"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "wrong"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def _walk_to_school_choice(hass: HomeAssistant):
    """Get as far as having picked a school."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick_school"}
    )
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_find_schools",
        return_value=SCHOOLS,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"query": "drei könig"}
        )
    assert result["step_id"] == "select_school"
    return result


async def test_school_without_sso_goes_to_password(hass: HomeAssistant) -> None:
    """A school with no providers should skip straight to the password form."""
    result = await _walk_to_school_choice(hass)
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_get_sso_methods",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"school": "5678"}
        )
    assert result["step_id"] == "credentials"


async def test_school_with_sso_offers_choice(hass: HomeAssistant) -> None:
    """A school with a provider should offer password or single sign-on."""
    result = await _walk_to_school_choice(hass)
    method = SsoMethod(kind="id-broker", value="stadtkoeln", institution_id=1234)
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_get_sso_methods",
        return_value=[method],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"school": "1234"}
        )
    assert result["step_id"] == "login_method"

    # Choosing the provider must show the URL the user has to open.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"method": "0"}
    )
    assert result["step_id"] == "sso"
    assert "kc_idp_hint=stadtkoeln" in result["description_placeholders"]["url"]
    assert "mobile-app=true" in result["description_placeholders"]["url"]


async def test_sso_paste_creates_entry(hass: HomeAssistant) -> None:
    """Pasting the final URL should store the user device, not a password."""
    result = await _walk_to_school_choice(hass)
    method = SsoMethod(kind="id-broker", value="stadtkoeln", institution_id=1234)
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_get_sso_methods",
        return_value=[method],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"school": "1234"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"method": "0"}
    )

    async def fake_device_login(self, device, reason="ha"):
        self.user = USER
        self.user_device = device

    pasted = "https://login.schulmanager-online.de/#userdevice=" + quote(
        json.dumps(DEVICE)
    )
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_login_with_user_device",
        fake_device_login,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": pasted}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USER_DEVICE] == DEVICE
    assert result["data"][CONF_INSTITUTION_ID] == 1234
    assert CONF_PASSWORD not in result["data"]


async def test_sso_bad_paste_shows_error(hass: HomeAssistant) -> None:
    """A URL without the fragment should be reported, not crash."""
    result = await _walk_to_school_choice(hass)
    method = SsoMethod(kind="oidc", value="iserv", institution_id=601)
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_get_sso_methods",
        return_value=[method],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"school": "1234"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"method": "0"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "https://example.com/no-fragment"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_user_device"}


async def test_no_schools_found(hass: HomeAssistant) -> None:
    """An empty search should say so rather than showing an empty list."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick_school"}
    )
    with patch(
        "custom_components.schulmanager_online.api.SchulmanagerClient."
        "async_find_schools",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"query": "zzzz"}
        )
    assert result["errors"] == {"base": "no_schools_found"}
