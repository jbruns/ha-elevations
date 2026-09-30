# Dashboards

Dashboards owns the household Displays. The glossary is in [CONTEXT.md](./CONTEXT.md).

## Weather forecast package

`packages/dashboards_weather_forecast.yaml` is optional support for Displays. It creates hourly and daily forecast sensors from `weather.get_forecasts`, with forecasts held in each sensor's `forecast` attribute. Replace the placeholder `weather.example` with this home's weather entity before installing the package.

No blueprint depends on this package (ADR 0005).

### Install

1. Copy `packages/dashboards_weather_forecast.yaml` into Home Assistant's packages, replacing `weather.example` with this home's weather entity.
2. Restart or reload template entities.
3. Check that `sensor.dashboards_hourly_forecast` and `sensor.dashboards_daily_forecast` have a `forecast` attribute. They refresh at startup and on the hour.

## Wallboard

`wallboard/display.yaml` renders the Wallboard from one view file and one file per section. Copy `wallboard/wallboard.local.example.yaml` to `wallboard/wallboard.local.yaml`, fill in this home's real values, then render with:

```sh
uv run python scripts/render-asset.py display \
  --source dashboards/wallboard/display.yaml \
  --overlay dashboards/wallboard/wallboard.local.yaml \
  --output dashboards/wallboard/build/wallboard.yaml
uv run python scripts/render-asset.py template \
  --template dashboards/wallboard/package.yaml \
  --overlay dashboards/wallboard/wallboard.local.yaml \
  --output dashboards/wallboard/build/package.yaml
```

`wallboard/package.yaml` is optional support for Wallboard-local roles:

- **Unlocked Entries**: `binary_sensor.wallboard_any_entry_unlocked` and `sensor.wallboard_unlocked_entries`.
- **Appliance Running**: `binary_sensor.wallboard_washer_active`, `binary_sensor.wallboard_dryer_active` and `binary_sensor.wallboard_dishwasher_active`.

The package uses the same overlay as the Display for the entry locks, entry labels and appliance power sensors. Nothing outside the Wallboard may read those Wallboard-local entities (ADR 0010).

### Prerequisites

- Safety's **Immediate Hazards** role: a binary sensor group helper that is `on` when an immediate hazard reports.
- Climate's **Exterior Doors** role: a binary sensor group helper that is `on` when an Exterior Door is open.
- The Wallboard support package above.
- The shared weather forecast package above, or an equivalent hourly forecast sensor.
- A School Day Dropdown helper with `true`/`false` options, and each child's Special Class Dropdown helper.
- Calendars for family events, appointments, trips/breaks, holidays, school closures, school lunch and birthdays.
- Todo lists for family reminders and shopping.
- Chore/points sensors, person entities, thermostat, garage-door cover, outdoor AQI, sunrise/sunset, entry locks and appliance power sensors named in the local overlay.
- Custom cards listed in `custom-cards.yaml`: Mushroom cards, Atomic Calendar Revive, Better Moment Card, Clock Weather Card and Hourly Weather Card.

### Install

1. Copy the rendered `wallboard/build/package.yaml` into Home Assistant's packages, then restart or reload template entities.
2. Check that the Wallboard-local role entities exist and update: `binary_sensor.wallboard_any_entry_unlocked`, `sensor.wallboard_unlocked_entries`, `binary_sensor.wallboard_washer_active`, `binary_sensor.wallboard_dryer_active` and `binary_sensor.wallboard_dishwasher_active`.
3. Paste the rendered `wallboard/build/wallboard.yaml` into a storage dashboard's raw configuration editor.
4. Verify the Wallboard shows the shared **Immediate Hazards** and **Exterior Doors** roles.

## Our Home

`our-home/display.yaml` assembles the Our Home Display from one source file per view in `our-home/views/`. Render it with a gitignored local overlay:

```sh
uv run python scripts/render-asset.py display \
  --source dashboards/our-home/display.yaml \
  --overlay dashboards/our-home/our-home.local.yaml \
  --output dashboards/our-home/build/our-home.yaml
```

Copy `our-home/our-home.local.example.yaml` to `our-home/our-home.local.yaml` and replace each placeholder with this home's real values. Never commit the real overlay or the rendered `build/` output.

### Prerequisites

- **Helpers**: the Front Door Snooze Date and time helpers, one per Recipient. The Conditions view works out its greeting from the time of day itself; it needs no helper.
- **Roles**: none. Our Home may consume Climate's **Exterior Doors** or Safety's **Immediate Hazards** only where the meaning exactly matches ADR 0010.
- **Packages**: none.
- **Custom cards**: Advanced Camera Card, Mushroom, bignumber-card, Stack In Card, button-card, Simple Thermostat and Valetudo Map Card, with versions listed in `custom-cards.yaml`.

### Install

1. Confirm the prerequisites above exist, including the custom cards.
2. Render `dashboards/our-home/build/our-home.yaml` from the local overlay.
3. Optionally diff the render against the live dashboard:

   ```sh
   uv run python scripts/diff-rendered-dashboard.py \
     --source dashboards/our-home/display.yaml \
     --overlay dashboards/our-home/our-home.local.yaml \
     --url-path our-home \
     --ssh-host hassio@ha.example.com
   ```

4. In Home Assistant, open Our Home (`url_path: our-home`) and paste the rendered YAML into the raw configuration editor.
5. Save, then verify all seven views: Conditions, Lighting, Climate, Cameras, Devices, TV and Front Door. The Conditions view's title shows the greeting for the time of day.
