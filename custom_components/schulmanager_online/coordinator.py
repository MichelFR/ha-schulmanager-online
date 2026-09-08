"""DataUpdateCoordinator for Schulmanager Online."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SchulmanagerAuthError, SchulmanagerClient, SchulmanagerError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type SchulmanagerConfigEntry = ConfigEntry[SchulmanagerCoordinator]


class SchulmanagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch all Schulmanager data in one poll and share it with the entities."""

    config_entry: SchulmanagerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SchulmanagerConfigEntry,
        client: SchulmanagerClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_setup(self) -> None:
        """One-time setup: log in before the first refresh."""
        try:
            await self.client.async_login()
        except SchulmanagerAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except SchulmanagerError as err:
            raise UpdateFailed(err) from err

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll the API."""
        try:
            async with asyncio.timeout(30):
                return await self.client.async_get_data()
        except SchulmanagerAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except SchulmanagerError as err:
            raise UpdateFailed(err) from err
