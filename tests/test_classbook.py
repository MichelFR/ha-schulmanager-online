"""Tests for the classbook module, modelled on the Berichte screen."""

from __future__ import annotations

import pytest

from custom_components.schulmanager_online.model import (
    count_classbook_entries,
    parse_absence_statistics,
)

# Mirrors "Abwesenheit nach Fächern" from a real report: 30 lessons in total
# and nothing missed, which must come out as 0.0% rather than a divide error.
CLEAN_TERM = [
    {"subject": {"name": "Deutsch"}, "absentLessons": 0, "totalLessons": 3},
    {"subject": {"name": "Erdkunde"}, "absentLessons": 0, "totalLessons": 3},
    {"subject": {"name": "Kunsterziehung"}, "absentLessons": 0, "totalLessons": 3},
    {"subject": {"name": "Mathematik"}, "absentLessons": 0, "totalLessons": 2},
    {"subject": None, "absentLessons": 0, "totalLessons": 8},
    {"subject": {"name": "Pause"}, "absentLessons": 0, "totalLessons": 2},
    {"subject": {"name": "Physik"}, "absentLessons": 0, "totalLessons": 2},
    {"subject": {"name": "Sport"}, "absentLessons": 0, "totalLessons": 2},
    {"subject": {"name": "Informatik"}, "absentLessons": 0, "totalLessons": 1},
    {
        "subject": {"name": "Sozialwissenschaften"},
        "absentLessons": 0,
        "totalLessons": 1,
    },
    {"subject": {"name": "Spanisch"}, "absentLessons": 0, "totalLessons": 2},
    {"subject": {"name": "Englisch"}, "absentLessons": 0, "totalLessons": 1},
]

WITH_ABSENCE = [
    {"subject": {"name": "Mathematik"}, "absentLessons": 3, "totalLessons": 10},
    {"subject": {"name": "Deutsch"}, "absentLessons": 1, "totalLessons": 10},
    {"subject": {"name": "Sport"}, "absentLessons": 0, "totalLessons": 5},
]


def test_clean_term_totals_match_the_report() -> None:
    """Thirty lessons, none missed: 0% and no division by zero."""
    stats = parse_absence_statistics(CLEAN_TERM, [], {})
    assert stats.total_lessons == 30
    assert stats.absent_lessons == 0
    assert stats.rate == 0.0


def test_total_is_summed_from_the_subject_rows() -> None:
    """The API returns no total, so it is derived the way the web app does."""
    stats = parse_absence_statistics(WITH_ABSENCE, [], {})
    assert stats.absent_lessons == 4
    assert stats.total_lessons == 25
    assert stats.rate == 16.0


def test_unexcused_comes_from_the_second_call() -> None:
    """unexcusedOnly=True is a separate request, summed the same way."""
    unexcused = [
        {"subject": {"name": "Mathematik"}, "absentLessons": 2, "totalLessons": 10}
    ]
    stats = parse_absence_statistics(WITH_ABSENCE, unexcused, {})
    assert stats.unexcused_lessons == 2
    assert stats.absent_lessons == 4


def test_absent_days_come_from_the_day_statistic() -> None:
    """Days are a different endpoint from lessons."""
    stats = parse_absence_statistics(
        WITH_ABSENCE, [], {"absentDays": 2.5, "unexcusedDays": 1}
    )
    assert stats.absent_days == 2.5
    assert stats.unexcused_days == 1


def test_subjects_sorted_worst_first() -> None:
    """The worst attendance should lead, as it does in the report."""
    stats = parse_absence_statistics(WITH_ABSENCE, [], {})
    assert [row["subject"] for row in stats.by_subject] == [
        "Mathematik",
        "Deutsch",
        "Sport",
    ]
    assert stats.by_subject[0]["rate"] == 30.0


def test_missing_subject_is_labelled() -> None:
    """The report calls an unattributed row 'Ohne Fach'."""
    stats = parse_absence_statistics(CLEAN_TERM, [], {})
    assert any(row["subject"] == "Ohne Fach" for row in stats.by_subject)


def test_empty_statistics_are_safe() -> None:
    """A school without the classbook module yields zeroes, not errors."""
    stats = parse_absence_statistics(None, None, None)
    assert stats.rate == 0.0
    assert stats.total_lessons == 0
    assert stats.by_subject == []


def test_garbage_rows_are_skipped() -> None:
    """Unexpected row shapes must not raise."""
    stats = parse_absence_statistics(
        [42, "nope", {"absentLessons": "x", "totalLessons": None}], [], {}
    )
    assert stats.total_lessons == 0
    assert stats.rate == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (7, 7),
        ([{"id": 1}, {"id": 2}], 2),
        ({"count": 3}, 3),
        ({"entries": [{"id": 1}]}, 1),
        ({"warnings": 2, "remarks": 1}, 3),
        ("nonsense", None),
        (True, None),
    ],
)
def test_entry_count_tolerates_unknown_shapes(raw, expected) -> None:
    """The entry statistic was never observed, so the parser stays defensive."""
    assert count_classbook_entries(raw) == expected
