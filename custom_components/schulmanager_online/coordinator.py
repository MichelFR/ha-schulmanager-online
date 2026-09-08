"""DataUpdateCoordinator for Schulmanager Online."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SchulmanagerAuthError, SchulmanagerClient, SchulmanagerError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .model import Lesson, parse_class_hours, parse_lessons, student_name

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
            )

        return SchulmanagerData(students=students, letters=raw.get("letters") or [])
