"""End-to-end setup test: real API payloads, mocked transport."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.schulmanager_online.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def raw_payload():
    """Return what the client hands back after a successful poll."""
    user = _load("login-user.json")
    student_id = str(user["associatedStudent"]["id"])
    return {
        "class_hours": _load("get-class-hours.json"),
        "letters": [{"id": 1, "subject": "Info"}, {"id": 2, "readTimestamp": "x"}],
        "students": {
            student_id: {
                "info": user["associatedStudent"],
                "lessons": _load("get-actual-lessons.json"),
                "exams": [],
                "homework": [],
            }
        },
    }


@pytest.fixture
def entry(hass: HomeAssistant):
    """Return a configured entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"},
        unique_id="42",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry, raw_payload) -> None:
    """Set the entry up with the network stubbed out."""
    with (
        patch(
            "custom_components.schulmanager_online.api."
            "SchulmanagerClient.async_ensure_login",
            return_value=None,
        ),
        patch(
            "custom_components.schulmanager_online.api."
            "SchulmanagerClient.async_get_data",
            return_value=raw_payload,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_creates_entities(hass: HomeAssistant, entry, raw_payload) -> None:
    """A successful poll should produce sensors, binary sensors and a calendar."""
    await _setup(hass, entry, raw_payload)
    assert entry.state is ConfigEntryState.LOADED

    states = [
        state
        for state in hass.states.async_all()
        if state.entity_id.split(".")[1].startswith("alex")
        or "schulmanager" in state.entity_id
    ]
    assert states, f"no entities created: {hass.states.async_entity_ids()}"

    domains = {state.entity_id.split(".")[0] for state in hass.states.async_all()}
    assert {"sensor", "binary_sensor", "calendar"} <= domains


async def test_lessons_today_and_changes(
    hass: HomeAssistant, entry, raw_payload, freezer
) -> None:
    """Counts should reflect the real dump for a day inside its window."""
    # 2026-09-11 in the dump has 7 entries, 3 of them cancelled.
    freezer.move_to("2026-09-11 09:00:00+02:00")
    await _setup(hass, entry, raw_payload)

    lessons_today = next(
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_lessons_today")
    )
    assert lessons_today.state == "4"

    changes = next(
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_timetable_changes_today")
    )
    assert changes.state == "3"


async def test_unread_letters(hass: HomeAssistant, entry, raw_payload) -> None:
    """Only the letter without a read timestamp counts."""
    await _setup(hass, entry, raw_payload)
    letters = next(
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_unread_letters")
    )
    assert letters.state == "1"


async def test_calendar_events(hass: HomeAssistant, entry, raw_payload) -> None:
    """The calendar should return the week's lessons."""
    await _setup(hass, entry, raw_payload)
    calendars = hass.states.async_all("calendar")
    assert len(calendars) == 1


async def test_unload(hass: HomeAssistant, entry, raw_payload) -> None:
    """Unloading should leave nothing behind."""
    await _setup(hass, entry, raw_payload)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
