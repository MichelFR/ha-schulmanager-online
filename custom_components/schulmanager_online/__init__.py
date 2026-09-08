"""The Schulmanager Online integration."""

from __future__ import annotations

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SchulmanagerClient
from .const import CONF_INSTITUTION_ID, CONF_USER_DEVICE
from .coordinator import SchulmanagerConfigEntry, SchulmanagerCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: SchulmanagerConfigEntry
) -> bool:
    """Set up Schulmanager Online from a config entry."""
    client = SchulmanagerClient(
        async_get_clientsession(hass),
        entry.data.get(CONF_EMAIL, ""),
        entry.data.get(CONF_PASSWORD, ""),
        institution_id=entry.data.get(CONF_INSTITUTION_ID),
    )
    # Single sign-on entries have no password, only this.
    client.user_device = entry.data.get(CONF_USER_DEVICE)

    coordinator = SchulmanagerCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # A changed poll interval only takes effect on a fresh coordinator.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(
    hass: HomeAssistant, entry: SchulmanagerConfigEntry
) -> None:
    """Reload the entry after its options changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: SchulmanagerConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
