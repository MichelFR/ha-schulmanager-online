"""Tests for the payload normalisation, driven by a real API dump."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from custom_components.schulmanager_online.model import (
    parse_class_hours,
    parse_lessons,
    students_of,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def class_hours():
    """Return the period grid from a real school."""
    return parse_class_hours(_load("get-class-hours.json"))


@pytest.fixture
def lessons(class_hours):
    """Return one real week of lessons."""
    return parse_lessons(_load("get-actual-lessons.json"), class_hours)


def test_class_hours_parsed(class_hours) -> None:
    """Every period should get a start and an end."""
    assert len(class_hours) == 13
    assert class_hours["1"].start.isoformat() == "07:55:00"
    assert class_hours["1"].end.isoformat() == "08:40:00"


def test_every_lesson_parsed(lessons) -> None:
    """No entry should be dropped, whatever its shape."""
    assert len(lessons) == 32
    assert all(lesson.day is not None for lesson in lessons)
    assert all(lesson.start is not None for lesson in lessons)


def test_lessons_are_sorted(lessons) -> None:
    """Class hour 10 must sort after 9, not after 1."""
    keys = [(lesson.day, int(lesson.class_hour)) for lesson in lessons]
    assert keys == sorted(keys)


def test_cancellations_detected(lessons) -> None:
    """The dump contains five cancelled lessons."""
    cancelled = [lesson for lesson in lessons if lesson.is_cancelled]
    assert len(cancelled) == 5
    # A cancelled lesson has no actualLesson, so the subject must come from
    # the original plan rather than being lost.
    assert all(lesson.subject for lesson in cancelled)


def test_substitution_keeps_both_sides(lessons) -> None:
    """A changed lesson should expose the new and the original room."""
    subs = [lesson for lesson in lessons if lesson.is_substitution]
    assert len(subs) == 1
    lesson = subs[0]
    assert lesson.room == "R113"
    assert lesson.original_room == "R114"
    attrs = lesson.as_attributes()
    assert attrs["original_room"] == "R114"
    assert attrs["is_cancelled"] is False


def test_regular_lesson_has_no_original_noise(lessons) -> None:
    """Unchanged lessons should not carry original_* attributes."""
    regular = next(
        lesson for lesson in lessons if not lesson.is_changed and not lesson.is_pseudo
    )
    assert "original_room" not in regular.as_attributes()


def test_breaks_are_not_lessons(lessons) -> None:
    """The lunch break is a pseudo subject and must not count as a lesson."""
    pseudo = [lesson for lesson in lessons if lesson.is_pseudo]
    assert len(pseudo) == 2
    assert all(not lesson.counts_as_lesson for lesson in pseudo)


def test_cancelled_lessons_do_not_count(lessons) -> None:
    """A cancelled lesson is on the timetable but is not happening."""
    assert all(not lesson.counts_as_lesson for lesson in lessons if lesson.is_cancelled)


def test_label(lessons) -> None:
    """The short label should combine subject and room."""
    lesson = next(lesson for lesson in lessons if lesson.day == date(2026, 9, 8))
    assert lesson.label == "KU (R108)"


def test_students_of_student_account() -> None:
    """A student account exposes itself through associatedStudent."""
    students = students_of(_load("login-user.json"))
    assert len(students) == 1
    assert isinstance(students[0]["id"], int)


def test_students_of_parent_account() -> None:
    """A parent account exposes one student per associatedParents entry."""
    user = {
        "associatedStudent": None,
        "associatedParents": [
            {"student": {"id": 1, "firstname": "A", "lastname": "B"}},
            {"student": {"id": 2, "firstname": "C", "lastname": "D"}},
        ],
    }
    assert [student["id"] for student in students_of(user)] == [1, 2]


def test_students_deduplicated() -> None:
    """The same child listed twice should appear once."""
    user = {
        "associatedParents": [
            {"student": {"id": 7}},
            {"student": {"id": 7}},
        ]
    }
    assert len(students_of(user)) == 1


def test_teacher_account_has_no_students() -> None:
    """A teacher login yields nothing to build entities from."""
    assert students_of({"associatedTeachers": [{"id": 1}]}) == []


def test_garbage_is_survivable() -> None:
    """Unexpected payloads must not raise."""
    assert parse_lessons(None, {}) == []
    assert parse_lessons([{"date": "nonsense"}, 42], {}) == []
    assert parse_class_hours([{"number": None}, "junk"]) == {}
    assert students_of(None) == []
