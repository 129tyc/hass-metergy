# Metergy for Home Assistant

Import Metergy electricity and water consumption into Home Assistant long-term
statistics for the Energy dashboard.

This integration is unofficial and is not affiliated with, endorsed by,
sponsored by, or supported by Metergy Solutions, MyMetergySolutions, or KUBRA.
Use it at your own responsibility and respect the provider's terms.

## Features

- Automatic login with username and password
- Electricity: hourly kWh import for the Energy dashboard
- Water: daily cold and hot water volume import, stored in liters
- Nightly scheduler at 03:10 local time
- Rolling scheduled backfill to re-import recently settled data
- Manual `metergy.backfill` service for historical ranges
- Persistent notifications for import and backfill failures
- Home Assistant events for automation triggers

## Installation

### HACS custom repository

This repository is not currently listed in the default HACS store. Add it as a
custom repository:

1. Open HACS.
2. Go to Integrations.
3. Open the three-dot menu and select Custom repositories.
4. Add `https://github.com/129tyc/hass-metergy`.
5. Select category `Integration`.
6. Install `Metergy`.
7. Restart Home Assistant.
8. Go to Settings > Devices & services > Add integration > Metergy.

### Manual install

1. Copy `custom_components/metergy` into your Home Assistant config directory
   as `custom_components/metergy`.
2. Restart Home Assistant.
3. Go to Settings > Devices & services > Add integration > Metergy.

## Configuration

- Meter ID: your Metergy meter/account ID
- Username: your Metergy account email
- Password: your Metergy account password
- Toggles: enable Electricity, Cold water, and/or Hot water
- Lag days: electricity defaults to 2, water defaults to 3
- Rolling backfill days: number of days to re-import during scheduled imports

Options flow lets you update credentials and import settings after setup.

## Backfill service

Service: `metergy.backfill`

Fields:

- `start`: start date, inclusive
- `end`: end date, inclusive
- `electricity`: import electricity data
- `cold_water`: import cold water data
- `hot_water`: import hot water data

Dates use `YYYY-MM-DD`.

## Events

The integration fires events that can be used in automations:

- `metergy_backfill_started`
- `metergy_backfill_completed`
- `metergy_backfill_failed`
- `metergy_import_started`
- `metergy_import_completed`
- `metergy_import_failed`

Event payloads include the configured `meter_id` so automations can distinguish
multiple configured meters.

Example:

```yaml
automation:
  - alias: "Notify on Metergy Import Failure"
    trigger:
      - platform: event
        event_type: metergy_import_failed
    action:
      - service: notify.mobile_app
        data:
          message: "Metergy import failed: {{ trigger.event.data.error }}"
```

## Privacy and logging

This integration stores credentials in Home Assistant's config entry storage,
the same place Home Assistant stores credentials for other integrations.

The integration does not intentionally log passwords, cookies, usernames, or
full request URLs. Event payloads and some Home Assistant UI metadata can include
the configured `meter_id`; review logs and diagnostics before sharing them.

Do not commit portal captures, HAR files, cookies, or raw API responses to a
public repository. The `.gitignore` includes rules to reduce the chance of doing
that accidentally.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m ruff check custom_components\metergy
python -m compileall -q custom_components\metergy
```

## Project layout

- `custom_components/metergy/`: Home Assistant integration package
- `hacs.json`: HACS metadata
- `.github/workflows/`: CI, HACS validation, and hassfest workflows

## Notes

- Timestamps align to local boundaries but are stored as UTC for Recorder.
- Water statistics are stored in liters.
- Brand assets are intentionally not based on any official Metergy logo.

## License

MIT
