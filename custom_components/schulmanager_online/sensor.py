"""Sensor platform for Schulmanager Online."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import EXAM_LOOKAHEAD_DAYS
from .coordinator import (
    SchulmanagerConfigEntry,
    SchulmanagerCoordinator,
    StudentData,
)
from .entity import SchulmanagerEntity
from .model import Lesson

type ValueFn = Callable[[StudentData, datetime], StateType | datetime]
type AttrFn = Callable[[StudentData, datetime], dict[str, Any] | None]


def _lesson_attributes(lesson: Lesson | None) -> dict[str, Any] | None:
    """Expose one lesson's detail, or nothing when there is no lesson."""
    return lesson.as_attributes() if lesson else None


def _exam_date(exam: dict[str, Any]) -> date | None:
    """Read an exam's date, tolerating a timestamp."""
    raw = exam.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _upcoming_exams(student: StudentData, today: date) -> list[dict[str, Any]]:
    """Return the exams from today onwards, soonest first."""
    dated = [
        (day, exam)
        for exam in student.exams
        if (day := _exam_date(exam)) is not None and day >= today
    ]
    dated.sort(key=lambda pair: pair[0])
    return [exam for _, exam in dated]


def _exam_summary(exam: dict[str, Any]) -> dict[str, Any]:
    """Render an exam for an attribute dict."""
    subject = exam.get("subject") or {}
    exam_type = exam.get("type") or {}
    return {
        "date": exam.get("date"),
        "subject": subject.get("name") if isinstance(subject, dict) else subject,
        "type": exam_type.get("name") if isinstance(exam_type, dict) else exam_type,
        "comment": exam.get("comment"),
    }


def _homework_due(item: dict[str, Any]) -> date | None:
    """Read a homework item's due date."""
    raw = item.get("date") or item.get("dueDate")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _open_homework(student: StudentData, today: date) -> list[dict[str, Any]]:
    """Return homework that is not done and not yet overdue-and-forgotten."""
    return [
        item
        for item in student.homework
        if not item.get("done") and (due := _homework_due(item)) and due >= today
    ]


def _homework_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Render a homework item for an attribute dict."""
    subject = item.get("subject") or {}
    return {
        "date": item.get("date"),
        "subject": subject.get("name") if isinstance(subject, dict) else subject,
        "homework": item.get("homework"),
    }


@dataclass(frozen=True, kw_only=True)
class SchulmanagerSensorDescription(SensorEntityDescription):
    """Describes a Schulmanager sensor."""

    value_fn: ValueFn
    attr_fn: AttrFn | None = None


SENSORS: tuple[SchulmanagerSensorDescription, ...] = (
    SchulmanagerSensorDescription(
        key="current_lesson",
        translation_key="current_lesson",
        value_fn=lambda student, now: (
            lesson.subject
            if (lesson := student.current_lesson(now)) is not None
            else None
        ),
        attr_fn=lambda student, now: _lesson_attributes(student.current_lesson(now)),
    ),
    SchulmanagerSensorDescription(
        key="next_lesson",
        translation_key="next_lesson",
        value_fn=lambda student, now: (
            lesson.subject if (lesson := student.next_lesson(now)) is not None else None
        ),
        attr_fn=lambda student, now: _lesson_attributes(student.next_lesson(now)),
    ),
    SchulmanagerSensorDescription(
        key="next_lesson_start",
        translation_key="next_lesson_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda student, now: (
            lesson.start if (lesson := student.next_lesson(now)) is not None else None
        ),
    ),
    SchulmanagerSensorDescription(
        key="lessons_today",
        translation_key="lessons_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: len(student.real_lessons_on(now.date())),
        attr_fn=lambda student, now: {
            "lessons": [
                lesson.as_attributes() for lesson in student.lessons_on(now.date())
            ]
        },
    ),
    SchulmanagerSensorDescription(
        key="school_start",
        translation_key="school_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda student, now: next(
            (
                lesson.start
                for lesson in student.real_lessons_on(now.date())
                if lesson.start
            ),
            None,
        ),
    ),
    SchulmanagerSensorDescription(
        key="school_end",
        translation_key="school_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda student, now: next(
            (
                lesson.end
                for lesson in reversed(student.real_lessons_on(now.date()))
                if lesson.end
            ),
            None,
        ),
    ),
    SchulmanagerSensorDescription(
        key="changes_today",
        translation_key="changes_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: len(student.changes_on(now.date())),
        attr_fn=lambda student, now: {
            "changes": [
                lesson.as_attributes() for lesson in student.changes_on(now.date())
            ]
        },
    ),
    SchulmanagerSensorDescription(
        key="changes_tomorrow",
        translation_key="changes_tomorrow",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: len(
            student.changes_on(now.date() + timedelta(days=1))
        ),
        attr_fn=lambda student, now: {
            "changes": [
                lesson.as_attributes()
                for lesson in student.changes_on(now.date() + timedelta(days=1))
            ]
        },
    ),
    SchulmanagerSensorDescription(
        key="upcoming_exams",
        translation_key="upcoming_exams",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: sum(
            1
            for exam in _upcoming_exams(student, now.date())
            if (day := _exam_date(exam))
            and day <= now.date() + timedelta(days=EXAM_LOOKAHEAD_DAYS)
        ),
        attr_fn=lambda student, now: {
            "exams": [
                _exam_summary(exam) for exam in _upcoming_exams(student, now.date())
            ]
        },
    ),
    SchulmanagerSensorDescription(
        key="next_exam",
        translation_key="next_exam",
        value_fn=lambda student, now: next(
            (exam.get("date") for exam in _upcoming_exams(student, now.date())),
            None,
        ),
        attr_fn=lambda student, now: next(
            (_exam_summary(exam) for exam in _upcoming_exams(student, now.date())),
            None,
        ),
    ),
    SchulmanagerSensorDescription(
        key="absence_rate",
        translation_key="absence_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: student.absence.rate,
        attr_fn=lambda student, now: student.absence.as_attributes(),
    ),
    SchulmanagerSensorDescription(
        key="absent_lessons",
        translation_key="absent_lessons",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda student, now: student.absence.absent_lessons,
        attr_fn=lambda student, now: {"by_subject": student.absence.by_subject},
    ),
    SchulmanagerSensorDescription(
        key="unexcused_lessons",
        translation_key="unexcused_lessons",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda student, now: student.absence.unexcused_lessons,
    ),
    SchulmanagerSensorDescription(
        key="absent_days",
        translation_key="absent_days",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda student, now: student.absence.absent_days,
        attr_fn=lambda student, now: {"unexcused_days": student.absence.unexcused_days},
    ),
    SchulmanagerSensorDescription(
        key="classbook_entries",
        translation_key="classbook_entries",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda student, now: student.classbook_entries,
    ),
    SchulmanagerSensorDescription(
        key="open_homework",
        translation_key="open_homework",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: len(_open_homework(student, now.date())),
        attr_fn=lambda student, now: {
            "homework": [
                _homework_summary(item) for item in _open_homework(student, now.date())
            ]
        },
    ),
    SchulmanagerSensorDescription(
        key="homework_due_tomorrow",
        translation_key="homework_due_tomorrow",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda student, now: sum(
            1
            for item in _open_homework(student, now.date())
            if _homework_due(item) == now.date() + timedelta(days=1)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchulmanagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SchulmanagerSensor(coordinator, student_id, description)
        for student_id in coordinator.data.students
        for description in SENSORS
    ]
    entities.append(SchulmanagerLettersSensor(coordinator, entry))
    entities.append(SchulmanagerNextEventSensor(coordinator, entry))
    async_add_entities(entities)


class SchulmanagerSensor(SchulmanagerEntity, SensorEntity):
    """A single per-student Schulmanager value."""

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
    def native_value(self) -> StateType | datetime:
        """Return the current value."""
        return self.entity_description.value_fn(self.student, dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the detail behind the value."""
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self.student, dt_util.now())


class SchulmanagerLettersSensor(SchulmanagerEntity, SensorEntity):
    """Unread letters, which belong to the account rather than a student."""

    _attr_translation_key = "unread_letters"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: SchulmanagerCoordinator, entry: SchulmanagerConfigEntry
    ) -> None:
        """Attach the sensor to the first student's device."""
        super().__init__(coordinator, next(iter(coordinator.data.students)))
        self._attr_unique_id = f"{entry.entry_id}_unread_letters"

    @property
    def native_value(self) -> int:
        """Return the number of unread letters."""
        return self.coordinator.data.unread_letters


class SchulmanagerNextEventSensor(SchulmanagerEntity, SensorEntity):
    """When the next school event starts, holidays excluded."""

    _attr_translation_key = "next_school_event"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: SchulmanagerCoordinator, entry: SchulmanagerConfigEntry
    ) -> None:
        """Attach the sensor to the first student's device."""
        super().__init__(coordinator, next(iter(coordinator.data.students)))
        self._attr_unique_id = f"{entry.entry_id}_next_school_event"

    @property
    def native_value(self) -> datetime | None:
        """Return the start of the next event."""
        event = self.coordinator.data.next_event(dt_util.now())
        if event is None:
            return None
        if isinstance(event.start, datetime):
            return event.start
        return datetime.combine(
            event.start, datetime.min.time(), tzinfo=dt_util.get_default_time_zone()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Describe the next event, plus everything on today."""
        now = dt_util.now()
        event = self.coordinator.data.next_event(now)
        data: dict[str, Any] = {
            "today": [
                item.as_attributes()
                for item in self.coordinator.data.events_on(now.date())
            ]
        }
        if event is not None:
            data.update(event.as_attributes())
        return data
