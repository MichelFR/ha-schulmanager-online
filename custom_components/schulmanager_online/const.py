"""Constants for the Schulmanager Online integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "schulmanager_online"

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=30)
