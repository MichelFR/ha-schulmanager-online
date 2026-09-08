# Ready-to-apply examples

Both files use `<PREFIX>` where the student's entity prefix goes. Home Assistant
derives that from the device name (the student's full name), so the real value
is only known once the integration has been added — check Developer Tools →
States.

**The entity *names* are localised too**, which caught me out on a German
instance: the sensor is `sensor.<prefix>_aktuelle_stunde`, not
`..._current_lesson`. So substituting the prefix alone is not enough — map the
suffixes as well:

| Translation key | German suffix |
| --- | --- |
| `current_lesson` | `aktuelle_stunde` |
| `next_lesson` / `next_lesson_starts` | `nachste_stunde` / `nachste_stunde_beginnt` |
| `lessons_today` | `stunden_heute` |
| `school_starts` / `school_ends` | `schulbeginn` / `schulende` |
| `timetable_changes_today` / `_tomorrow` | `planabweichungen_heute` / `_morgen` |
| `upcoming_exams` / `next_exam` | `anstehende_klassenarbeiten` / `nachste_klassenarbeit` |
| `open_homework` / `homework_due_tomorrow` | `offene_hausaufgaben` / `hausaufgaben_fur_morgen` |
| `unread_letters` | `ungelesene_elternbriefe` |
| `absence_rate` / `absent_lessons` | `abwesenheitsquote` / `fehlstunden` |
| `unexcused_lessons` / `absent_days` | `unentschuldigte_fehlstunden` / `fehltage` |
| `classbook_entries` | `klassenbucheintrage` |
| `next_school_event` | `nachster_schultermin` |
| `timetable` / `school_calendar` | `stundenplan` / `schulkalender` |
| `school_today` / `at_school` / `school_holiday` | `schule_heute` / `in_der_schule` / `ferien` |

Also: don't build an entity id by string concatenation inside a Jinja template
(`'sensor.' ~ prefix ~ '_changes_' ~ key`) — a find-and-replace over the config
will mangle it. Write the ids out in full.

| File | What it is |
| --- | --- |
| `dashboard.json` | A four-view *Schule* dashboard: Heute, Stundenplan, Termine, Fehlzeiten |
| `automations.yaml` | The current-lesson Live Activity and the substitution/cancellation alert |

## Notes on the automations

**Live Activity (current lesson).** Needs iOS 17.2+, HA Core 2026.7+, and the
feature switched on once in the companion app under Settings → Live Activities;
it is still flagged beta. `chronometer` makes the countdown tick on the device
rather than pushing an update every minute, and `silent: true` keeps updates
from making noise. **Live Activities do not work on iPad** — an Apple
limitation, documented by Home Assistant — so that automation targets the
iPhone only.

Worth knowing: iOS has a separate *push-to-start budget*. Starting and ending
activities repeatedly — as happens while testing — exhausts it, after which new
activities fail **silently**: the automation succeeds and nothing is logged. It
replenishes on its own; a reboot does not help.

**"Critical but silent" is not possible on iOS.** Critical alerts are designed
to always make a sound, and the `volume: 0` trick is undocumented and has been
broken since iOS 16 (home-assistant/iOS#2204, closed as not planned). The
substitution alert therefore uses `interruption-level: time-sensitive` with
`sound: none`: genuinely silent, and it still cuts through Focus and Do Not
Disturb. It does **not** override the physical mute switch — only a true
critical alert does that, and that one would beep.

The alert also ignores the transition out of `unknown`, or it would re-announce
every change on each Home Assistant restart.
