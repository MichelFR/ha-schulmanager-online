"""Tests for the calendar module, modelled on a real month view.

The reference is a September 2026 screenshot of the school calendar: an
all-day "Sommerferien" band, timed evening events, a three-day trip, and a
single-day public holiday.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from custom_components.schulmanager_online.model import (
    day_of,
    parse_event_categories,
    parse_events,
)

CATEGORIES = [
    {"id": 1, "name": "JGS EF", "color": "#1a7d34"},
    {"id": 2, "name": "SCHULE (öffentlich)", "color": "#d9534f"},
]

PAYLOAD = {
    "nonRecurringEvents": [
        {
            "id": 1,
            "summary": "Sommerferien",
            "start": "2026-08-31T00:00:00.000Z",
            "end": "2026-09-01T00:00:00.000Z",
            "allDay": True,
            "categoryId": -1,
        },
        {
            "id": 2,
            "summary": "JGS 5-Q2: Beginn des Unterrichts",
            "start": "2026-09-02T05:55:00.000Z",
            "end": "2026-09-02T07:00:00.000Z",
            "allDay": False,
            "categoryId": 2,
            "location": "Aula",
        },
        {
            "id": 3,
            "summary": "1. Schulpflegschaftssitzung",
            "start": "2026-09-23T16:00:00.000Z",
            "end": "2026-09-23T18:00:00.000Z",
            "allDay": False,
            "categoryId": 2,
            "description": "Elternvertretung",
        },
        {
            "id": 4,
            "summary": "JGS 10/EF: Trier-Fahrt",
            "start": "2026-09-28T00:00:00.000Z",
            "end": "2026-09-30T00:00:00.000Z",
            "allDay": True,
            "categoryId": 1,
        },
        {
            "id": 5,
            "summary": "Tag der Deutschen Einheit",
            "start": "2026-10-03T00:00:00.000Z",
            "end": "2026-10-03T00:00:00.000Z",
            "allDay": True,
            "categoryId": -1,
        },
    ],
    "recurringEvents": [],
}


@pytest.fixture
def categories():
    """Return the category index."""
    return parse_event_categories(CATEGORIES)


@pytest.fixture
def events(categories):
    """Return the parsed month."""
    return parse_events(PAYLOAD, categories)


def test_all_events_parsed(events) -> None:
    """Every entry should survive."""
    assert len(events) == 5


def test_categories_include_the_invented_holiday_one(categories) -> None:
    """Holidays have no server-side category, so one is synthesised."""
    assert categories[-1] == "Ferien/Feiertage"
    assert categories[1] == "JGS EF"


def test_timed_event_keeps_its_time(events) -> None:
    """An evening meeting must not become all-day."""
    meeting = next(e for e in events if e.summary.startswith("1. Schul"))
    assert meeting.all_day is False
    assert isinstance(meeting.start, datetime)
    assert meeting.description == "Elternvertretung"
    assert meeting.category == "SCHULE (öffentlich)"


def test_single_day_all_day_event(events) -> None:
    """A one-day holiday spans exactly one day, end exclusive."""
    unity = next(e for e in events if e.summary.startswith("Tag der"))
    assert unity.all_day is True
    assert unity.start == date(2026, 10, 3)
    # Home Assistant treats an all-day end as exclusive.
    assert unity.end == date(2026, 10, 4)
    assert unity.is_holiday is True
    assert unity.covers(date(2026, 10, 3))
    assert not unity.covers(date(2026, 10, 4))


def test_multi_day_all_day_event(events) -> None:
    """The three-day trip covers Mon-Wed and stops there."""
    trip = next(e for e in events if "Trier" in e.summary)
    assert trip.start == date(2026, 9, 28)
    assert trip.end == date(2026, 10, 1)
    for day in (28, 29, 30):
        assert trip.covers(date(2026, 9, day)), day
    assert not trip.covers(date(2026, 10, 1))
    assert not trip.covers(date(2026, 9, 27))


def test_holidays_are_flagged(events) -> None:
    """Only the Ferien/Feiertage entries count as holidays."""
    holidays = {e.summary for e in events if e.is_holiday}
    assert holidays == {"Sommerferien", "Tag der Deutschen Einheit"}


def test_sorted_all_day_first(events) -> None:
    """Events sort by day, with all-day entries first."""
    days = [day_of(e.start) for e in events]
    assert days == sorted(days)


def test_weekly_recurrence_expanded(categories) -> None:
    """A weekly series should become one event per occurrence."""
    payload = {
        "nonRecurringEvents": [],
        "recurringEvents": [
            {
                "id": 10,
                "summary": "Chor",
                "start": "2026-09-02T14:00:00.000Z",
                "end": "2026-09-02T15:00:00.000Z",
                "allDay": False,
                "categoryId": 1,
                "recurrencePattern": {
                    "frequency": "Weekly",
                    "interval": 1,
                    "start": "2026-09-02T00:00:00.000Z",
                    "end": "2026-09-30T00:00:00.000Z",
                },
            }
        ],
    }
    events = parse_events(payload, categories)
    assert [e.start.date().day for e in events] == [2, 9, 16, 23, 30]
    # Each occurrence needs its own uid or Home Assistant collapses them.
    assert len({e.uid for e in events}) == 5


def test_every_other_week_honours_interval(categories) -> None:
    """interval=2 should skip a week."""
    payload = {
        "nonRecurringEvents": [],
        "recurringEvents": [
            {
                "id": 11,
                "summary": "Schulband",
                "start": "2026-09-02T14:00:00.000Z",
                "end": "2026-09-02T15:00:00.000Z",
                "allDay": False,
                "recurrencePattern": {
                    "frequency": "Weekly",
                    "interval": 2,
                    "start": "2026-09-02T00:00:00.000Z",
                    "end": "2026-09-30T00:00:00.000Z",
                },
            }
        ],
    }
    events = parse_events(payload, categories)
    assert [e.start.date().day for e in events] == [2, 16, 30]


def test_monthly_by_weekday(categories) -> None:
    """'first Wednesday of the month' should land on Wednesdays."""
    payload = {
        "nonRecurringEvents": [],
        "recurringEvents": [
            {
                "id": 12,
                "summary": "Schulkonferenz",
                "start": "2026-09-02T16:00:00.000Z",
                "end": "2026-09-02T18:00:00.000Z",
                "allDay": False,
                "recurrencePattern": {
                    "frequency": "Monthly",
                    "interval": 1,
                    "weekday": 3,  # JavaScript getDay(): Wednesday
                    "weekdayInMonth": 1,
                    "start": "2026-09-02T00:00:00.000Z",
                    "end": "2026-12-31T00:00:00.000Z",
                },
            }
        ],
    }
    events = parse_events(payload, categories)
    assert all(e.start.weekday() == 2 for e in events)  # Python: Wednesday == 2
    assert [e.start.date().isoformat() for e in events] == [
        "2026-09-02",
        "2026-10-07",
        "2026-11-04",
        "2026-12-02",
    ]


def test_multi_day_recurrence_keeps_its_span(categories) -> None:
    """A two-day series must stay two days on every occurrence."""
    payload = {
        "nonRecurringEvents": [],
        "recurringEvents": [
            {
                "id": 13,
                "summary": "Projekttage",
                "start": "2026-09-07T00:00:00.000Z",
                "end": "2026-09-08T00:00:00.000Z",
                "allDay": True,
                "recurrencePattern": {
                    "frequency": "Monthly",
                    "interval": 1,
                    "monthday": 7,
                    "start": "2026-09-07T00:00:00.000Z",
                    "end": "2026-11-07T00:00:00.000Z",
                },
            }
        ],
    }
    events = parse_events(payload, categories)
    assert len(events) == 3
    for event in events:
        assert (event.end - event.start).days == 2  # two days, end exclusive


def test_window_filters_occurrences(categories) -> None:
    """Occurrences outside the requested window are dropped."""
    payload = {
        "nonRecurringEvents": [],
        "recurringEvents": [
            {
                "id": 14,
                "summary": "Chor",
                "start": "2026-09-02T14:00:00.000Z",
                "end": "2026-09-02T15:00:00.000Z",
                "allDay": False,
                "recurrencePattern": {
                    "frequency": "Weekly",
                    "interval": 1,
                    "start": "2026-09-02T00:00:00.000Z",
                    "end": "2026-12-31T00:00:00.000Z",
                },
            }
        ],
    }
    events = parse_events(payload, categories, date(2026, 9, 10), date(2026, 9, 24))
    assert [e.start.date().isoformat() for e in events] == ["2026-09-16", "2026-09-23"]


def test_open_ended_series_is_capped(categories) -> None:
    """A series with no end must not expand forever."""
    payload = {
        "nonRecurringEvents": [],
        "recurringEvents": [
            {
                "id": 15,
                "summary": "Endlos",
                "start": "2026-09-02T14:00:00.000Z",
                "end": "2026-09-02T15:00:00.000Z",
                "allDay": False,
                "recurrencePattern": {
                    "frequency": "Daily",
                    "interval": 1,
                    "start": "2026-09-02T00:00:00.000Z",
                    "end": None,
                },
            }
        ],
    }
    events = parse_events(payload, categories)
    assert 0 < len(events) <= 1000


def test_garbage_is_survivable() -> None:
    """Unexpected payloads must not raise."""
    assert parse_events(None) == []
    assert parse_events({}) == []
    assert parse_events({"nonRecurringEvents": [42, {"summary": "x"}]}) == []
    assert (
        parse_events({"recurringEvents": [{"id": 1, "start": "2026-01-01T00:00:00Z"}]})
        == []
    )
    assert parse_event_categories(None) == {-1: "Ferien/Feiertage"}


def test_day_of_handles_datetime_and_date() -> None:
    """A datetime subclasses date, so the order of the isinstance test matters."""
    assert day_of(datetime(2026, 9, 3, 14, 30)) == date(2026, 9, 3)
    assert day_of(date(2026, 9, 3)) == date(2026, 9, 3)
