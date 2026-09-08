"""Binary sensor platform for Schulmanager Online."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import (
    SchulmanagerConfigEntry,
    SchulmanagerCoordinator,
    StudentData,
)
from .entity import SchulmanagerEntity


def _at_school(student: StudentData, now: datetime) -> bool:
    """Return True between the first and last lesson of the day."""
    lessons = student.real_lessons_on(now.date())
    starts = [lesson.start for lesson in lessons if lesson.start]
    ends = [lesson.end for lesson in lessons if lesson.end]
    if not starts or not ends:
        return False
    return min(starts) <= now < max(ends)


@dataclass(frozen=True, kw_only=True)
class SchulmanagerBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Schulmanager binary sensor."""

    is_on_fn: Callable[[StudentData, datetime], bool]


BINARY_SENSORS: tuple[SchulmanagerBinarySensorDescription, ...] = (
    SchulmanagerBinarySensorDescription(
        key="school_today",
        translation_key="school_today",
        is_on_fn=lambda student, now: bool(student.real_lessons_on(now.date())),
    ),
    SchulmanagerBinarySensorDescription(
        key="at_school",
        translation_key="at_school",
        is_on_fn=_at_school,
    ),
    SchulmanagerBinarySensorDescription(
        key="has_changes_today",
        translation_key="has_changes_today",
        is_on_fn=lambda student, now: bool(student.changes_on(now.date())),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchulmanagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        SchulmanagerBinarySensor(coordinator, student_id, description)
        for student_id in coordinator.data.students
        for description in BINARY_SENSORS
    ]
    entities.append(SchulmanagerHolidaySensor(coordinator, entry))
    async_add_entities(entities)


class SchulmanagerBinarySensor(SchulmanagerEntity, BinarySensorEntity):
    """A yes/no answer about one student's school day."""

    entity_description: SchulmanagerBinarySensorDescription

    def __init__(
        self,
        coordinator: SchulmanagerCoordinator,
        student_id: str,
        description: SchulmanagerBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, student_id)
        self.entity_description = description
        self._attr_unique_id = f"{student_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return the current state."""
        return self.entity_description.is_on_fn(self.student, dt_util.now())


class SchulmanagerHolidaySensor(SchulmanagerEntity, BinarySensorEntity):
    """Whether today falls in a school holiday.

    Handy as an automation condition: no school run, no 06:30 alarm.
    """

    _attr_translation_key = "school_holiday"

    def __init__(
        self, coordinator: SchulmanagerCoordinator, entry: SchulmanagerConfigEntry
    ) -> None:
        """Attach the sensor to the first student's device."""
        super().__init__(coordinator, next(iter(coordinator.data.students)))
        self._attr_unique_id = f"{entry.entry_id}_school_holiday"

    @property
    def is_on(self) -> bool:
        """Return True while a holiday covers today."""
        return self.coordinator.data.holiday_on(dt_util.now().date()) is not None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Name the holiday and when it ends."""
        holiday = self.coordinator.data.holiday_on(dt_util.now().date())
        if holiday is None:
            return None
        return {"name": holiday.summary, "ends": holiday.end.isoformat()}
