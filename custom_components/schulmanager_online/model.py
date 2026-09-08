"""Turn raw Schulmanager payloads into something entities can use.

``get-actual-lessons`` returns three shapes discriminated by ``type``, and the
difference matters: a cancelled lesson carries no ``actualLesson`` at all, while
a changed one carries both what was scheduled (``originalLessons``) and what will
actually happen (``actualLesson``). Everything here is pure data massaging so it
can be unit tested without touching the network.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from .const import (
    HOLIDAY_CATEGORY_ID,
    HOLIDAY_CATEGORY_NAME,
    LESSON_CANCELLED,
    LESSON_CHANGED,
)


def _parse_date(value: Any) -> date | None:
    """Parse an ISO date, tolerating a full timestamp or nonsense."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_time(value: Any) -> time | None:
    """Parse an ``HH:MM:SS`` class hour boundary."""
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class ClassHour:
    """One period of the school day."""

    number: str
    start: time | None
    end: time | None


def parse_class_hours(raw: Any) -> dict[str, ClassHour]:
    """Index the period grid by its (string) number.

    Each entry also carries ``fromByDay``/``untilByDay``, but every school seen
    so far repeats the same times for all seven days and the index convention is
    unverified, so the flat ``from``/``until`` are used.
    """
    hours: dict[str, ClassHour] = {}
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        raw_number = entry.get("number")
        if raw_number is None or raw_number == "":
            continue
        number = str(raw_number)
        hours[number] = ClassHour(
            number=number,
            start=_parse_time(entry.get("from")),
            end=_parse_time(entry.get("until")),
        )
    return hours


def _people(raw: Any) -> list[str]:
    """Render a teacher list as readable names."""
    names = []
    for person in raw or []:
        if not isinstance(person, dict):
            continue
        full = f"{person.get('firstname', '')} {person.get('lastname', '')}".strip()
        names.append(full or person.get("abbreviation") or "?")
    return names


def _room(raw: Any) -> str | None:
    """Pull a room name out of a lesson."""
    if isinstance(raw, dict):
        name = raw.get("name")
        return str(name) if name else None
    return None


@dataclass(frozen=True)
class Lesson:
    """One lesson as it will actually take place (or not)."""

    day: date
    class_hour: str
    start: datetime | None
    end: datetime | None
    subject: str | None
    subject_abbreviation: str | None
    teachers: list[str] = field(default_factory=list)
    room: str | None = None
    is_cancelled: bool = False
    is_substitution: bool = False
    is_new: bool = False
    is_pseudo: bool = False
    """True for filler entries such as the lunch break, not a real lesson."""

    comment: str | None = None
    original_subject: str | None = None
    original_teachers: list[str] = field(default_factory=list)
    original_room: str | None = None

    @property
    def counts_as_lesson(self) -> bool:
        """Return True for a lesson that actually takes place.

        Excludes the lunch break and other pseudo subjects, and anything
        cancelled, so counts and "next lesson" stay meaningful.
        """
        return not self.is_pseudo and not self.is_cancelled

    @property
    def is_changed(self) -> bool:
        """Return True when this lesson deviates from the plan."""
        return self.is_cancelled or self.is_substitution or self.is_new

    @property
    def label(self) -> str:
        """Return a short human label, e.g. ``M (R113)``."""
        subject = self.subject_abbreviation or self.subject or "?"
        return f"{subject} ({self.room})" if self.room else subject

    def as_attributes(self) -> dict[str, Any]:
        """Render the lesson for an entity attribute dict."""
        data: dict[str, Any] = {
            "date": self.day.isoformat(),
            "class_hour": self.class_hour,
            "subject": self.subject,
            "subject_abbreviation": self.subject_abbreviation,
            "teachers": self.teachers,
            "room": self.room,
            "is_cancelled": self.is_cancelled,
            "is_substitution": self.is_substitution,
        }
        if self.start:
            data["start"] = self.start.isoformat()
        if self.end:
            data["end"] = self.end.isoformat()
        if self.comment:
            data["comment"] = self.comment
        # Only mention the original when it actually differs, so the attribute
        # stays quiet for the 95% of lessons that go to plan.
        if self.is_substitution:
            if self.original_subject and self.original_subject != self.subject:
                data["original_subject"] = self.original_subject
            if self.original_room and self.original_room != self.room:
                data["original_room"] = self.original_room
            if self.original_teachers and self.original_teachers != self.teachers:
                data["original_teachers"] = self.original_teachers
        return data


def _combine(day: date, moment: time | None, tzinfo: Any) -> datetime | None:
    """Attach a class hour boundary to its day in the local timezone."""
    if moment is None:
        return None
    return datetime.combine(day, moment, tzinfo=tzinfo)


def parse_lessons(
    raw: Any, class_hours: dict[str, ClassHour], tzinfo: Any = None
) -> list[Lesson]:
    """Normalise ``get-actual-lessons`` into a sorted list of lessons."""
    lessons: list[Lesson] = []

    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        day = _parse_date(entry.get("date"))
        if day is None:
            continue

        number = str((entry.get("classHour") or {}).get("number", ""))
        hour = class_hours.get(number)

        kind = entry.get("type")
        # A cancelled lesson has no actualLesson, so fall back to the plan to
        # still show what would have taken place.
        originals = entry.get("originalLessons") or []
        original = originals[0] if isinstance(originals, list) and originals else {}
        actual = entry.get("actualLesson") or original

        subject = (actual.get("subject") or {}) if isinstance(actual, dict) else {}
        original_subject = (original.get("subject") or {}) if original else {}

        lessons.append(
            Lesson(
                day=day,
                class_hour=number,
                start=_combine(day, hour.start if hour else None, tzinfo),
                end=_combine(day, hour.end if hour else None, tzinfo),
                subject=subject.get("name"),
                subject_abbreviation=subject.get("abbreviation"),
                teachers=_people(actual.get("teachers") if actual else None),
                room=_room(actual.get("room") if actual else None),
                is_cancelled=bool(entry.get("isCancelled")) or kind == LESSON_CANCELLED,
                is_substitution=bool(entry.get("isSubstitution"))
                or kind == LESSON_CHANGED,
                is_new=bool(entry.get("isNew")),
                is_pseudo=bool(subject.get("isPseudoSubject")),
                comment=entry.get("comment")
                or (actual.get("comment") if isinstance(actual, dict) else None),
                original_subject=original_subject.get("name"),
                original_teachers=_people(
                    original.get("teachers") if original else None
                ),
                original_room=_room(original.get("room") if original else None),
            )
        )

    lessons.sort(key=lambda lesson: (lesson.day, _hour_sort_key(lesson.class_hour)))
    return lessons


def _hour_sort_key(number: str) -> tuple[int, str]:
    """Sort class hours numerically where possible ('10' after '9')."""
    try:
        return (int(number), "")
    except ValueError:
        return (10**6, number)


def students_of(user: Any) -> list[dict[str, Any]]:
    """Collect the students a user may look at.

    A student account carries ``associatedStudent``; a parent account carries one
    ``associatedParents`` entry per child.
    """
    if not isinstance(user, dict):
        return []

    found: list[dict[str, Any]] = []
    own = user.get("associatedStudent")
    if isinstance(own, dict):
        found.append(own)
    for parent in user.get("associatedParents") or []:
        if isinstance(parent, dict) and isinstance(parent.get("student"), dict):
            found.append(parent["student"])

    # A parent listed twice for the same child would otherwise duplicate.
    unique: dict[int, dict[str, Any]] = {}
    for student in found:
        if isinstance(student.get("id"), int):
            unique.setdefault(student["id"], student)
    return list(unique.values())


def student_name(student: dict[str, Any]) -> str:
    """Return a display name for a student."""
    name = f"{student.get('firstname', '')} {student.get('lastname', '')}".strip()
    return name or f"Student {student.get('id')}"


# --------------------------------------------------------------------------- #
# calendar module
# --------------------------------------------------------------------------- #


def _parse_moment(value: Any) -> datetime | None:
    """Parse an ISO timestamp from the calendar module."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc_date(moment: datetime) -> date:
    """Return the calendar date an all-day boundary refers to.

    All-day events come back as UTC midnight; the web app shifts them by the
    viewer's offset purely to make them render on the right day. Taking the UTC
    date gets the same answer without the sleight of hand.
    """
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(UTC).date()


def _js_weekday(day: date) -> int:
    """Convert to JavaScript's getDay(), where Sunday is 0."""
    return (day.weekday() + 1) % 7


def _add_months(day: date, count: int) -> date:
    """Add whole months, clamping to the end of a short month."""
    month_index = day.month - 1 + count
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last = monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _advance(pattern: dict[str, Any], current: date) -> date | None:
    """Step one recurrence forward, mirroring the web app's rules."""
    frequency = pattern.get("frequency")
    if frequency == "Daily":
        return current + timedelta(days=1)
    if frequency == "Weekly":
        return current + timedelta(days=7)
    if frequency == "Yearly":
        return _add_months(current, 12)
    if frequency == "Monthly":
        if pattern.get("monthday") is not None:
            return _add_months(current, 1)
        weekday = pattern.get("weekday")
        nth = pattern.get("weekdayInMonth")
        if weekday is None or nth is None:
            return None
        # "the nth <weekday> of the month": start at the first of next month
        # and walk forward, exactly as the web app does.
        candidate = _add_months(date(current.year, current.month, 1), 1)
        while _js_weekday(candidate) != weekday:
            candidate += timedelta(days=1)
        return candidate + timedelta(days=7 * (int(nth) - 1))
    return None


def _occurrences(pattern: dict[str, Any], limit: int = 1000) -> list[date]:
    """Expand a recurrence pattern into its start dates."""
    start = _parse_moment(pattern.get("start"))
    if start is None:
        return []
    first = _as_utc_date(start)

    end_raw = _parse_moment(pattern.get("end"))
    # The web app caps an open-ended series at five years.
    last = _as_utc_date(end_raw) if end_raw else _add_months(first, 60)

    interval = pattern.get("interval") or 1
    try:
        interval = max(1, int(interval))
    except (TypeError, ValueError):
        interval = 1

    dates: list[date] = []
    current = first
    while current <= last and len(dates) < limit:
        dates.append(current)
        nxt: date | None = current
        for _ in range(interval):
            nxt = _advance(pattern, nxt) if nxt is not None else None
            if nxt is None:
                return dates
        # A pattern that fails to move forward would otherwise spin forever.
        if nxt <= current:
            break
        current = nxt
    return dates


def day_of(value: datetime | date) -> date:
    """Return the calendar day of a bound.

    ``datetime`` subclasses ``date``, so an ``isinstance(x, date)`` test is true
    for both and silently leaves a datetime in place — which then fails to
    compare against a real date. Test for ``datetime`` first.
    """
    return value.date() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class SchoolEvent:
    """One entry from the calendar module."""

    uid: str
    summary: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    category: str | None = None
    category_id: int | None = None
    description: str | None = None
    location: str | None = None
    organizer: str | None = None

    @property
    def is_holiday(self) -> bool:
        """Return True for a holiday or public holiday entry."""
        return self.category_id == HOLIDAY_CATEGORY_ID

    def covers(self, day: date) -> bool:
        """Return True when this event covers a given day."""
        start, end = day_of(self.start), day_of(self.end)
        if self.all_day:
            # end is exclusive for an all-day event, as Home Assistant expects.
            return start <= day < end
        return start <= day <= end

    def as_attributes(self) -> dict[str, Any]:
        """Render the event for an entity attribute dict."""
        data: dict[str, Any] = {
            "summary": self.summary,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
            "category": self.category,
        }
        for key, value in (
            ("description", self.description),
            ("location", self.location),
            ("organizer", self.organizer),
        ):
            if value:
                data[key] = value
        return data


def parse_event_categories(raw: Any) -> dict[int, str]:
    """Index the category names by id."""
    categories: dict[int, str] = {HOLIDAY_CATEGORY_ID: HOLIDAY_CATEGORY_NAME}
    for entry in raw or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), int):
            categories[entry["id"]] = str(entry.get("name") or entry["id"])
    return categories


def _build_event(
    raw: dict[str, Any],
    categories: dict[int, str],
    start: datetime,
    end: datetime,
    suffix: str = "",
) -> SchoolEvent | None:
    """Turn one raw event plus resolved bounds into a SchoolEvent."""
    all_day = bool(raw.get("allDay"))
    if all_day:
        first = _as_utc_date(start)
        # Home Assistant wants an exclusive end; the API's is inclusive.
        final = _as_utc_date(end) + timedelta(days=1)
        if final <= first:
            final = first + timedelta(days=1)
        event_start: datetime | date = first
        event_end: datetime | date = final
    else:
        event_start, event_end = start, end
        if event_end < event_start:
            event_end = event_start

    category_id = raw.get("categoryId")
    category_id = category_id if isinstance(category_id, int) else None

    summary = str(raw.get("summary") or "").strip() or "Termin"
    return SchoolEvent(
        uid=f"{raw.get('id')}{suffix}",
        summary=summary,
        start=event_start,
        end=event_end,
        all_day=all_day,
        category=categories.get(category_id) if category_id is not None else None,
        category_id=category_id,
        description=raw.get("description") or None,
        location=raw.get("location") or None,
        organizer=raw.get("organizer") or None,
    )


def parse_events(
    raw: Any,
    categories: dict[int, str] | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
) -> list[SchoolEvent]:
    """Normalise ``get-events-for-user`` into a flat, sorted event list.

    The endpoint splits its answer: ``nonRecurringEvents`` are ready to use,
    while ``recurringEvents`` carry a pattern the client is expected to expand.
    """
    if not isinstance(raw, dict):
        return []
    categories = categories or {HOLIDAY_CATEGORY_ID: HOLIDAY_CATEGORY_NAME}
    events: list[SchoolEvent] = []

    for entry in raw.get("nonRecurringEvents") or []:
        if not isinstance(entry, dict):
            continue
        start = _parse_moment(entry.get("start"))
        end = _parse_moment(entry.get("end")) or start
        if start is None or end is None:
            continue
        if (event := _build_event(entry, categories, start, end)) is not None:
            events.append(event)

    for entry in raw.get("recurringEvents") or []:
        if not isinstance(entry, dict):
            continue
        start = _parse_moment(entry.get("start"))
        end = _parse_moment(entry.get("end")) or start
        pattern = entry.get("recurrencePattern")
        if start is None or end is None or not isinstance(pattern, dict):
            continue

        span = (_as_utc_date(end) - _as_utc_date(start)).days
        for occurrence in _occurrences(pattern):
            if window_start and occurrence + timedelta(days=span) < window_start:
                continue
            if window_end and occurrence > window_end:
                continue
            # Keep the original time of day, move the date.
            moved_start = datetime.combine(occurrence, start.timetz())
            moved_end = datetime.combine(
                occurrence + timedelta(days=span), end.timetz()
            )
            event = _build_event(
                entry,
                categories,
                moved_start,
                moved_end,
                suffix=f"-{occurrence.isoformat()}",
            )
            if event is not None:
                events.append(event)

    events.sort(key=_event_sort_key)
    return events


def _event_sort_key(event: SchoolEvent) -> tuple[date, int, str]:
    """Sort by day, all-day first, then title."""
    return (day_of(event.start), 0 if event.all_day else 1, event.summary)
