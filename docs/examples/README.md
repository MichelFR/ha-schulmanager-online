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
it is still flagged beta. **Live Activities do not work on iPad** — an Apple
limitation, documented by Home Assistant — so that automation targets the
iPhone only.

**One activity per day, not one per lesson.** The first version cleared the
activity whenever a lesson ended and started a fresh one at the next bell —
observed in the traces as `unknown` → `clear_notification` at 09:30, then a new
start at 09:50. That is roughly seven push-to-starts a day, and iOS's
push-to-start budget is finite: once exhausted, new activities fail **silently**
— the automation succeeds and nothing is logged. So breaks now *update* the
same tag with "Pause · danach <Fach>" and only school's end clears it.

**The progress bar needs the five-minute tick.** `progress` moves only when a
push arrives, and during a lesson nothing about the sensor changes — so without
a `time_pattern` trigger the bar would sit frozen at whatever it was when the
lesson started. `chronometer` is different: it ticks down on the device between
pushes, which is why the countdown stays live for free. Verified mid-lesson:
Erdkunde 09:50–10:35 rendered `progress: 39` and `when: 1646` at 10:07.

**"Critical but silent" is not possible on iOS.** Critical alerts are designed
to always make a sound, and the `volume: 0` trick is undocumented and has been
broken since iOS 16 (home-assistant/iOS#2204, closed as not planned). The
substitution alert therefore uses `interruption-level: time-sensitive` with
`sound: none`: genuinely silent, and it still cuts through Focus and Do Not
Disturb. It does **not** override the physical mute switch — only a true
critical alert does that, and that one would beep.

The alert also ignores the transition out of `unknown`, or it would re-announce
every change on each Home Assistant restart.

## The seven automations actually deployed

All in the *Benachrichtigungen* category. `automations.yaml` in this directory
holds the first two in full; the rest follow the same shape.

| Automation | Trigger | To |
| --- | --- | --- |
| Aktuelle Stunde Live Activity | `aktuelle_stunde` changes | iPhone |
| Vertretung oder Ausfall | `planabweichungen_heute` / `_morgen` rise | iPhone + iPad |
| Morgen-Briefing | 06:45, only on school days | iPhone + iPad |
| Abend-Briefing | 19:30, only if there is something to say | iPhone + iPad |
| Neuer Elternbrief | `ungelesene_elternbriefe` above 0 | parent + iPhone + iPad |
| Unentschuldigte Fehlstunden | value rises | parent + iPhone + iPad |
| Neuer Klassenbucheintrag | value rises | parent + iPhone + iPad |

Every alert goes to the student as well as the parent — the three above started
parent-only and were widened. The shared title/body is computed once into
`variables` and reused across the `parallel` branches, so the wording cannot
drift between devices.

### Two rules worth copying

**Every state-triggered alert carries a 07:00–21:00 time condition.** Polling
is every five minutes, so without it a change published at 03:00 buzzes a phone
at 03:00.

**Every one ignores the transition out of `unknown`.** On a Home Assistant
restart each sensor goes `unknown` → value, which without the guard
re-announces every outstanding change as if it were new.

### On the template conditions

The best-practice checker flags `condition: template` and it is right to — an
OR of three numeric comparisons in the Abend-Briefing was rewritten as a native
`condition: or` of `numeric_state`. The remaining templates compare
`trigger.from_state` with `trigger.to_state` to detect a *rise* and to ignore
attribute-only changes; neither has a native equivalent, so they stay.

That last point also protects the Live Activity: a plain state trigger fires on
attribute changes too, and iOS's push-to-start budget is finite.
