# Schulmanager Online — Home Assistant integration

Unofficial custom integration that pulls homework, exams, timetable and letters
from [Schulmanager Online](https://www.schulmanager-online.de/) into Home Assistant.

> **Status: working, unofficial.** Password and single sign-on login, the
> timetable (including substitutions and cancellations), exams, homework and
> letters. The protocol is written up in [`docs/api.md`](docs/api.md).

## Installation

### HACS (custom repository)
1. HACS → Integrations → ⋮ → *Custom repositories*
2. Add this repository with category **Integration**
3. Install, then restart Home Assistant

### Manual
Copy `custom_components/schulmanager_online` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Settings → Devices & Services → **Add integration** → *Schulmanager Online*.
The flow asks how you want to log in:

- **Email and password** — the usual case.
- **Select a school first** — search the public school directory, then pick
  from whatever that school actually offers. Schools that federate to an
  identity provider (IServ, Office 365, Univention ID-Broker, logoDIDACT,
  VIDIS) get a single sign-on option here.

Single sign-on needs one manual step, because your password goes to the
school's identity provider and never through Home Assistant: open the link the
flow shows, sign in, then paste back the address your browser ends up on. It
carries a long-lived *user device* credential, which is stored instead of a
password.

There is no YAML configuration.

## Entities

One device per student, plus a shared letters sensor.

| Entity | What it gives you |
| --- | --- |
| `sensor.…_current_lesson` | Subject happening now; room, teacher and period in attributes |
| `sensor.…_next_lesson` | Next subject, skipping breaks and cancellations |
| `sensor.…_next_lesson_starts` | Timestamp — good for notifications |
| `sensor.…_lessons_today` | Count of lessons that actually take place; full day in attributes |
| `sensor.…_school_starts` / `_school_ends` | Timestamps of the first and last lesson today |
| `sensor.…_timetable_changes_today` / `_tomorrow` | Cancellations and substitutions, with before/after detail |
| `sensor.…_upcoming_exams` | Exams in the next 14 days; all known exams in attributes |
| `sensor.…_next_exam` | Date of the next exam |
| `sensor.…_open_homework` | Open homework count, with the list in attributes |
| `sensor.…_homework_due_tomorrow` | What has to be done tonight |
| `sensor.…_unread_letters` | Unread letters on the account |
| `binary_sensor.…_school_today` | Whether there are any lessons today |
| `binary_sensor.…_at_school` | Between the first and last lesson |
| `binary_sensor.…_timetable_changed_today` | Something deviates from the plan |
| `calendar.…_timetable` | The timetable as a calendar; cancellations marked ❌, substitutions ↷ |

Breaks arrive as lessons with a pseudo subject (`PAUSE`); they appear on the
calendar but never count as a lesson or as "next lesson".

### Automation ideas

```yaml
# Tell everyone when tomorrow's plan changes
automation:
  - triggers:
      - trigger: numeric_state
        entity_id: sensor.alex_timetable_changes_tomorrow
        above: 0
    actions:
      - action: notify.family
        data:
          message: >-
            {{ state_attr('sensor.alex_timetable_changes_tomorrow', 'changes')
               | map(attribute='subject') | join(', ') }} changed tomorrow.
```

## Known limitations

- **Two-factor accounts are not supported yet.** The API accepts a
  `twoFactorCode`, but the config flow has nowhere to ask for one.
- **Teacher and administrator logins are rejected**, because every entity is
  built around a student.
- A single sign-on entry cannot be re-authenticated in place; if the user
  device stops working, remove and re-add the integration.
- A newly enrolled sibling appears only after reloading the integration.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
```

To poke the API by hand — it asks how you want to log in, then offers a menu of
endpoints and can dump every response to disk:

```bash
python tools/smo_cli.py --dump dumps/
```

The protocol is written up in [`docs/api.md`](docs/api.md).

## Layout

| File | Purpose |
| --- | --- |
| `manifest.json` | Integration metadata (`version` is mandatory for custom integrations) |
| `__init__.py` | `async_setup_entry` / `async_unload_entry`, stores the coordinator in `entry.runtime_data` |
| `api.py` | All HTTP access, raises `SchulmanagerAuthError` / `SchulmanagerError` |
| `model.py` | Payload normalisation — the three lesson shapes, students, class hours |
| `coordinator.py` | `DataUpdateCoordinator` — one poll shared by every entity |
| `config_flow.py` | UI setup + reauth |
| `entity.py` | Base `CoordinatorEntity` with device info |
| `sensor.py` | Sensor platform driven by entity descriptions |
| `binary_sensor.py` | School-today / at-school / changed-today |
| `calendar.py` | The timetable as a calendar |
| `quality_scale.yaml` | Progress against the Bronze tier rules |
| `../docs/api.md` | Reverse-engineered protocol notes |
| `../tools/smo_cli.py` | Interactive CLI probe for the API |

## Disclaimer

Not affiliated with or endorsed by Schulmanager Online. It uses an unofficial
API and may break at any time. Use at your own risk.

## Releasing

Releases are automated. Publish a GitHub release with a tag like `v0.2.0`; the
[release workflow](.github/workflows/release.yml) rewrites `manifest.json` with
the tag version, zips the integration and attaches `schulmanager_online.zip`.
HACS installs from that ZIP (`zip_release` in `hacs.json`).
