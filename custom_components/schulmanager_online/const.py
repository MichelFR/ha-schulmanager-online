"""Constants for the Schulmanager Online integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "schulmanager_online"

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=30)

# The config entry stores either a password or a user device (single sign-on
# accounts have no password to store).
CONF_INSTITUTION_ID: Final = "institution_id"
CONF_USER_DEVICE: Final = "user_device"

# How much timetable to keep in memory. One week back is enough to answer
# "what happened today" after midnight; four weeks ahead covers the calendar.
TIMETABLE_DAYS_BEFORE: Final = 7
TIMETABLE_DAYS_AFTER: Final = 28

# Window for the "upcoming exams" sensor.
EXAM_LOOKAHEAD_DAYS: Final = 14

# Lesson types returned by ``get-actual-lessons``.
LESSON_REGULAR: Final = "regularLesson"
LESSON_CANCELLED: Final = "cancelledLesson"
LESSON_CHANGED: Final = "changedLesson"
