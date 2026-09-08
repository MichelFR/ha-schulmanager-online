# Schulmanager Online — Home Assistant integration

Unofficial custom integration that pulls homework, exams, timetable and letters
from [Schulmanager Online](https://www.schulmanager-online.de/) into Home Assistant.

> **Status: placeholder.** The structure is in place; the API client
> (`api.py`) is not implemented yet.

## Installation

### HACS (custom repository)
1. HACS → Integrations → ⋮ → *Custom repositories*
2. Add this repository with category **Integration**
3. Install, then restart Home Assistant

### Manual
Copy `custom_components/schulmanager_online` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Settings → Devices & Services → **Add integration** → *Schulmanager Online*,
then enter the account email and password. There is no YAML configuration.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
```

## Layout

| File | Purpose |
| --- | --- |
| `manifest.json` | Integration metadata (`version` is mandatory for custom integrations) |
| `__init__.py` | `async_setup_entry` / `async_unload_entry`, stores the coordinator in `entry.runtime_data` |
| `api.py` | All HTTP access, raises `SchulmanagerAuthError` / `SchulmanagerError` |
| `coordinator.py` | `DataUpdateCoordinator` — one poll shared by every entity |
| `config_flow.py` | UI setup + reauth |
| `entity.py` | Base `CoordinatorEntity` with device info |
| `sensor.py` | Sensor platform driven by entity descriptions |
| `quality_scale.yaml` | Progress against the Bronze tier rules |

## Disclaimer

Not affiliated with or endorsed by Schulmanager Online. It uses an unofficial
API and may break at any time. Use at your own risk.

## Releasing

Releases are automated. Publish a GitHub release with a tag like `v0.2.0`; the
[release workflow](.github/workflows/release.yml) rewrites `manifest.json` with
the tag version, zips the integration and attaches `schulmanager_online.zip`.
HACS installs from that ZIP (`zip_release` in `hacs.json`).
