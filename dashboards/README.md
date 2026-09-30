# Dashboards

Dashboards owns the household Displays. The glossary is in [CONTEXT.md](./CONTEXT.md).

## Weather forecast package

`packages/dashboards_weather_forecast.yaml` is optional support for Displays. It creates hourly and daily forecast sensors from `weather.get_forecasts`, with forecasts held in each sensor's `forecast` attribute. Replace the placeholder `weather.example` with this home's weather entity before installing the package.

No blueprint depends on this package (ADR 0005).

### Cutover

1. Back up Home Assistant.
2. Copy `packages/dashboards_weather_forecast.yaml` into Home Assistant's packages, replacing `weather.example` with this home's weather entity.
3. Restart or reload package-backed YAML as usual for that installation.
4. Check that `sensor.dashboards_hourly_forecast` and `sensor.dashboards_daily_forecast` have a `forecast` attribute.
5. Remove the old `configuration.yaml` trigger-based template block that created the same forecast sensors.

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
- Existing household helpers for the greeting/day-of-week era of the Wallboard, including the old true/false School Day dropdown and each child's Special Class dropdown. These stay faithful here and are replaced by later tickets.
- Calendars for family events, appointments, trips/breaks, holidays, school closures, school lunch and birthdays.
- Todo lists for family reminders and shopping.
- Chore/points sensors, person entities, thermostat, garage-door cover, outdoor AQI, sunrise/sunset, entry locks and appliance power sensors named in the local overlay.
- Custom cards listed in `custom-cards.yaml`: Mushroom cards, Atomic Calendar Revive, Better Moment Card, Clock Weather Card and Hourly Weather Card.

### Wallboard cutover

1. Back up Home Assistant.
2. Render `wallboard/package.yaml` with `wallboard.local.yaml`, then copy `wallboard/build/package.yaml` into Home Assistant's packages.
3. Restart or reload package-backed YAML as usual for that installation.
4. Check that the Wallboard-local role entities exist and update: `binary_sensor.wallboard_any_entry_unlocked`, `sensor.wallboard_unlocked_entries`, `binary_sensor.wallboard_washer_active`, `binary_sensor.wallboard_dryer_active` and `binary_sensor.wallboard_dishwasher_active`.
5. Render the Wallboard with `wallboard.local.yaml` and paste `wallboard/build/wallboard.yaml` into the storage dashboard at `dashboard-wallboard`.
6. Verify the Wallboard shows the shared **Immediate Hazards** and **Exterior Doors** roles.
7. Remove the old Wallboard-only hazards/open-doors groups and the old Wallboard package only after the pasted Display and new package are working.

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
- **Roles**: none are consumed by Our Home yet. Keep the live entities as-is for this faithful extraction; later restructuring can consume Climate's **Exterior Doors** or Safety's **Immediate Hazards** only where the meaning exactly matches ADR 0010.
- **Packages**: none for the faithful Our Home extraction. The shared weather forecast package remains documented above for Displays that consume those forecast sensors.
- **Custom cards**: Advanced Camera Card, Mushroom, bignumber-card, Stack In Card, button-card, Simple Thermostat and Valetudo Map Card, with versions listed in `custom-cards.yaml`.

### Cutover

Do not cut over without an explicit maintainer request.

1. Back up Home Assistant.
2. Confirm the prerequisites above exist, including the custom cards.
3. Render `dashboards/our-home/build/our-home.yaml` from the local overlay.
4. Optionally run the local baseline diff:

   ```sh
   uv run python scripts/diff-rendered-dashboard.py \
     --source dashboards/our-home/display.yaml \
     --overlay dashboards/our-home/our-home.local.yaml \
     --url-path our-home \
     --ssh-host hassio@ha.example.com
   ```

5. In Home Assistant, open Our Home (`url_path: our-home`) and paste the rendered YAML into the raw configuration editor.
6. Save, then verify all seven views: Conditions, Lighting, Climate, Cameras, Devices, TV and Front Door. The Conditions view's title shows the greeting for the time of day.
7. Retire the greeting and day-of-week automations and their input select helpers. Our Home no longer reads either helper. The legacy Overview dashboard still reads both, so retire it first, or accept that its cards break.
