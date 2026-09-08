"""Turn raw Schulmanager payloads into something entities can use.

``get-actual-lessons`` returns three shapes discriminated by ``type``, and the
difference matters: a cancelled lesson carries no ``actualLesson`` at all, while
a changed one carries both what was scheduled (``originalLessons``) and what will
actually happen (``actualLesson``). Everything here is pure data massaging so it
can be unit tested without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from .const import LESSON_CANCELLED, LESSON_CHANGED


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
