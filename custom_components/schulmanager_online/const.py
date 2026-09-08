"""Constants for the Schulmanager Online integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "schulmanager_online"

# Five minutes keeps a cancelled first period useful on the morning it
# happens. At one batched request per poll that is ~290 calls a day against
# the 800 the API allows, so there is plenty of headroom.
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 5
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)
MIN_SCAN_INTERVAL_MINUTES: Final = 1
MAX_SCAN_INTERVAL_MINUTES: Final = 1440

# The config entry stores either a password or a user device (single sign-on
# accounts have no password to store).
CONF_INSTITUTION_ID: Final = "institution_id"
CONF_USER_DEVICE: Final = "user_device"

# How much timetable to keep in memory. One week back is enough to answer
# "what happened today" after midnight; four weeks ahead covers the calendar.
TIMETABLE_DAYS_BEFORE: Final = 7
TIMETABLE_DAYS_AFTER: Final = 28

# The calendar module is not capped the way the timetable is, and school
# events (trips, holidays, parents' evenings) are planned months ahead.
CALENDAR_DAYS_BEFORE: Final = 14
CALENDAR_DAYS_AFTER: Final = 180

# The web app invents this category for holidays rather than the server
# returning one, so holidays are recognised by its id.
HOLIDAY_CATEGORY_ID: Final = -1
HOLIDAY_CATEGORY_NAME: Final = "Ferien/Feiertage"

# Classbook statistics are reported per term, so the term has to be fetched
# before they can be asked for.
CLASSBOOK_STATISTIC_TYPE: Final = "sum-all"

# Window for the "upcoming exams" sensor.
EXAM_LOOKAHEAD_DAYS: Final = 14

# Lesson types returned by ``get-actual-lessons``.
LESSON_REGULAR: Final = "regularLesson"
LESSON_CANCELLED: Final = "cancelledLesson"
LESSON_CHANGED: Final = "changedLesson"
