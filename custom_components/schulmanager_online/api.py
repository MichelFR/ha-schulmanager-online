"""Thin client for the (unofficial) Schulmanager Online API.

Placeholder: the real request/response handling still needs to be implemented.
Keep every HTTP call in here so the rest of the integration stays testable.
"""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, ClientSession

API_BASE = "https://login.schulmanager-online.de/api"


class SchulmanagerError(Exception):
    """Generic error talking to Schulmanager Online."""


class SchulmanagerAuthError(SchulmanagerError):
    """Credentials were rejected."""


class SchulmanagerClient:
    """Minimal async API client."""

    def __init__(self, session: ClientSession, email: str, password: str) -> None:
        """Store credentials and the shared aiohttp session."""
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None

    async def async_login(self) -> None:
        """Authenticate and cache the bearer token."""
        raise NotImplementedError

    async def async_get_data(self) -> dict[str, Any]:
        """Return everything the coordinator needs in a single call."""
        raise NotImplementedError
