"""Sensor platform for Schulmanager Online.

Placeholder: descriptions are examples until the API payload is mapped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SchulmanagerConfigEntry, SchulmanagerCoordinator
from .entity import SchulmanagerEntity


@dataclass(frozen=True, kw_only=True)
class SchulmanagerSensorDescription(SensorEntityDescription):
    """Describes a Schulmanager sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[SchulmanagerSensorDescription, ...] = (
    SchulmanagerSensorDescription(
        key="open_homework",
        translation_key="open_homework",
        value_fn=lambda data: len(data.get("homework", [])),
    ),
    SchulmanagerSensorDescription(
        key="upcoming_exams",
        translation_key="upcoming_exams",
        value_fn=lambda data: len(data.get("exams", [])),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchulmanagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SchulmanagerSensor(coordinator, student_id, description)
        for student_id in coordinator.data
        for description in SENSORS
    )


class SchulmanagerSensor(SchulmanagerEntity, SensorEntity):
    """A single Schulmanager value."""

    entity_description: SchulmanagerSensorDescription

    def __init__(
        self,
        coordinator: SchulmanagerCoordinator,
        student_id: str,
        description: SchulmanagerSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, student_id)
        self.entity_description = description
        self._attr_unique_id = f"{student_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data[self._student_id])
