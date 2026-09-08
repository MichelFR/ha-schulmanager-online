"""DataUpdateCoordinator for Schulmanager Online."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SchulmanagerAuthError, SchulmanagerClient, SchulmanagerError
from .const import DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN
from .model import (
    AbsenceStatistics,
    Lesson,
    SchoolEvent,
    count_classbook_entries,
    parse_absence_statistics,
    parse_class_hours,
    parse_event_categories,
    parse_events,
    parse_lessons,
    student_name,
)

_LOGGER = logging.getLogger(__name__)

type SchulmanagerConfigEntry = ConfigEntry[SchulmanagerCoordinator]


@dataclass
class StudentData:
    """Everything known about one student, ready for entities."""

    student_id: str
    name: str
    info: dict[str, Any]
    lessons: list[Lesson] = field(default_factory=list)
    exams: list[dict[str, Any]] = field(default_factory=list)
    homework: list[dict[str, Any]] = field(default_factory=list)
    absence: AbsenceStatistics = field(default_factory=AbsenceStatistics)
    classbook_entries: int | None = None

    def lessons_on(self, day: date) -> list[Lesson]:
        """Return every timetable entry for one day, breaks included."""
        return [lesson for lesson in self.lessons if lesson.day == day]

    def real_lessons_on(self, day: date) -> list[Lesson]:
        """Return the lessons on one day that actually take place."""
        return [lesson for lesson in self.lessons_on(day) if lesson.counts_as_lesson]

    def changes_on(self, day: date) -> list[Lesson]:
        """Return the cancellations and substitutions on one day."""
        return [
            lesson
            for lesson in self.lessons_on(day)
            if lesson.is_changed and not lesson.is_pseudo
        ]

    def current_lesson(self, now: datetime) -> Lesson | None:
        """Return the lesson happening right now, if any."""
        for lesson in self.real_lessons_on(now.date()):
            if lesson.start and lesson.end and lesson.start <= now < lesson.end:
                return lesson
        return None

    def next_lesson(self, now: datetime) -> Lesson | None:
        """Return the next lesson that has not started yet."""
        upcoming = [
            lesson
            for lesson in self.lessons
            if lesson.counts_as_lesson and lesson.start and lesson.start > now
        ]
        return min(upcoming, key=lambda lesson: lesson.start) if upcoming else None


@dataclass
class SchulmanagerData:
    """The whole poll result."""

    students: dict[str, StudentData] = field(default_factory=dict)
    letters: list[dict[str, Any]] = field(default_factory=list)
    events: list[SchoolEvent] = field(default_factory=list)

    def events_on(self, day: date) -> list[SchoolEvent]:
        """Return every school event covering one day."""
        return [event for event in self.events if event.covers(day)]

    def holiday_on(self, day: date) -> SchoolEvent | None:
        """Return the holiday covering one day, if any."""
        return next((event for event in self.events_on(day) if event.is_holiday), None)

    def next_event(self, now: datetime) -> SchoolEvent | None:
        """Return the next event that has not started yet."""
        upcoming = [
            event
            for event in self.events
            if _event_start(event, now.tzinfo) > now and not event.is_holiday
        ]
        return min(
            upcoming, key=lambda event: _event_start(event, now.tzinfo), default=None
        )

    @property
    def unread_letters(self) -> int:
        """Count letters that have not been confirmed as read.

        The payload marks a read letter with a ``readTimestamp`` or a truthy
        ``read`` flag depending on the school's configuration, so both count.
        """
        return sum(
            1
            for letter in self.letters
            if not letter.get("readTimestamp") and not letter.get("read")
        )


class SchulmanagerCoordinator(DataUpdateCoordinator[SchulmanagerData]):
    """Fetch all Schulmanager data in one poll and share it with the entities."""

    config_entry: SchulmanagerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SchulmanagerConfigEntry,
        client: SchulmanagerClient,
    ) -> None:
        """Initialise the coordinator."""
        minutes = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client

    async def _async_setup(self) -> None:
        """One-time setup: log in before the first refresh."""
        try:
            await self.client.async_ensure_login()
        except SchulmanagerAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except SchulmanagerError as err:
            raise UpdateFailed(err) from err

    async def _async_update_data(self) -> SchulmanagerData:
        """Poll the API and parse the result."""
        try:
            async with asyncio.timeout(60):
                raw = await self.client.async_get_data()
        except SchulmanagerAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except SchulmanagerError as err:
            raise UpdateFailed(err) from err

        return self._parse(raw)

    def _parse(self, raw: dict[str, Any]) -> SchulmanagerData:
        """Turn the raw payload into the parsed view entities expect."""
        class_hours = parse_class_hours(raw.get("class_hours"))
        tzinfo = dt_util.get_default_time_zone()

        students: dict[str, StudentData] = {}
        for student_id, payload in (raw.get("students") or {}).items():
            info = payload.get("info") or {}
            students[student_id] = StudentData(
                student_id=student_id,
                name=student_name(info),
                info=info,
                lessons=parse_lessons(payload.get("lessons"), class_hours, tzinfo),
                exams=payload.get("exams") or [],
                homework=payload.get("homework") or [],
                absence=parse_absence_statistics(
                    payload.get("absence_by_subject"),
                    payload.get("unexcused_by_subject"),
                    payload.get("absence_days"),
                ),
                classbook_entries=count_classbook_entries(
                    payload.get("classbook_entries")
                ),
            )

        window = raw.get("calendar_window") or []
        window_start = _as_date(window[0]) if len(window) > 0 else None
        window_end = _as_date(window[1]) if len(window) > 1 else None

        return SchulmanagerData(
            students=students,
            letters=raw.get("letters") or [],
            events=parse_events(
                raw.get("events"),
                parse_event_categories(raw.get("event_categories")),
                window_start,
                window_end,
            ),
        )


def _as_date(value: Any) -> date | None:
    """Parse an ISO date, tolerating anything else."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _event_start(event: SchoolEvent, tzinfo: Any) -> datetime:
    """Return a comparable start for an event, all-day included."""
    if isinstance(event.start, datetime):
        return event.start
    return datetime.combine(event.start, datetime.min.time(), tzinfo=tzinfo)
