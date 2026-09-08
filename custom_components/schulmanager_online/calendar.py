"""Calendar platform for Schulmanager Online.

A timetable is calendar shaped, so this is the entity that shows it best: one
event per lesson, with substitutions and cancellations spelled out in the title.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import (
    SchulmanagerConfigEntry,
    SchulmanagerCoordinator,
)
from .entity import SchulmanagerEntity
from .model import Lesson, SchoolEvent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchulmanagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one timetable calendar per student."""
    coordinator = entry.runtime_data
    entities: list[CalendarEntity] = [
        SchulmanagerCalendar(coordinator, student_id)
        for student_id in coordinator.data.students
    ]
    # The calendar module belongs to the account, not to one student.
    entities.append(SchulmanagerEventCalendar(coordinator, entry))
    async_add_entities(entities)


class SchulmanagerCalendar(SchulmanagerEntity, CalendarEntity):
    """One student's timetable as a calendar."""

    _attr_translation_key = "timetable"

    def __init__(self, coordinator: SchulmanagerCoordinator, student_id: str) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{student_id}_timetable"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the lesson happening now, or the next one."""
        now = dt_util.now()
        lesson = self.student.current_lesson(now) or self.student.next_lesson(now)
        return _to_event(lesson) if lesson else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return every lesson overlapping the requested window."""
        events = []
        for lesson in self.student.lessons:
            if lesson.start is None or lesson.end is None:
                continue
            if lesson.end <= start_date or lesson.start >= end_date:
                continue
            events.append(_to_event(lesson))
        return events


def _to_event(lesson: Lesson) -> CalendarEvent:
    """Render one lesson as a calendar event."""
    subject = lesson.subject or lesson.subject_abbreviation or "Lesson"
    if lesson.is_cancelled:
        summary = f"❌ {subject} (cancelled)"
    elif lesson.is_substitution:
        summary = f"↷ {subject}"
    else:
        summary = subject

    details = [f"Lesson {lesson.class_hour}"]
    if lesson.teachers:
        details.append(", ".join(lesson.teachers))
    if lesson.is_substitution:
        if lesson.original_room and lesson.original_room != lesson.room:
            details.append(f"Room was {lesson.original_room}")
        if lesson.original_teachers and lesson.original_teachers != lesson.teachers:
            details.append(f"Originally {', '.join(lesson.original_teachers)}")
    if lesson.comment:
        details.append(lesson.comment)

    return CalendarEvent(
        start=lesson.start,
        end=lesson.end,
        summary=summary,
        location=lesson.room,
        description="\n".join(details),
    )


class SchulmanagerEventCalendar(SchulmanagerEntity, CalendarEntity):
    """The school's own calendar: trips, parents' evenings, holidays."""

    _attr_translation_key = "school_calendar"

    def __init__(
        self, coordinator: SchulmanagerCoordinator, entry: SchulmanagerConfigEntry
    ) -> None:
        """Attach the calendar to the first student's device."""
        super().__init__(coordinator, next(iter(coordinator.data.students)))
        self._attr_unique_id = f"{entry.entry_id}_school_calendar"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next school event."""
        now = dt_util.now()
        today = now.date()
        # An event running right now beats one that merely starts later.
        ongoing = [
            item
            for item in self.coordinator.data.events_on(today)
            if not item.is_holiday
        ]
        if ongoing:
            return _event_to_calendar_event(ongoing[0])
        upcoming = self.coordinator.data.next_event(now)
        return _event_to_calendar_event(upcoming) if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return every school event overlapping the requested window."""
        events = []
        for item in self.coordinator.data.events:
            start, end = _bounds(item, start_date.tzinfo)
            if end <= start_date or start >= end_date:
                continue
            events.append(_event_to_calendar_event(item))
        return events


def _bounds(event: SchoolEvent, tzinfo: Any) -> tuple[datetime, datetime]:
    """Return comparable datetime bounds for an event, all-day included."""
    if isinstance(event.start, datetime):
        start = event.start
    else:
        start = datetime.combine(event.start, datetime.min.time(), tzinfo=tzinfo)
    if isinstance(event.end, datetime):
        end = event.end
    else:
        end = datetime.combine(event.end, datetime.min.time(), tzinfo=tzinfo)
    return start, end


def _event_to_calendar_event(event: SchoolEvent) -> CalendarEvent:
    """Render one school event as a calendar event."""
    details = []
    if event.category:
        details.append(event.category)
    if event.description:
        details.append(event.description)
    if event.organizer:
        details.append(event.organizer)

    return CalendarEvent(
        start=event.start,
        end=event.end,
        summary=event.summary,
        location=event.location,
        description="\n".join(details) or None,
        uid=event.uid,
    )
