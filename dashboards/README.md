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

`wallboard/display.yaml` renders the Wallboard from one sections view and one file per section. The view has a full-width glance band above the Household Schedule and a rail beside it. Copy `wallboard/wallboard.local.example.yaml` to `wallboard/wallboard.local.yaml`, fill in this home's real values, then render with:

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

`wallboard/package.yaml` is optional support for Wallboard-local roles. The Wallboard does not show these roles yet:

- **Unlocked Entries**: `binary_sensor.wallboard_any_entry_unlocked` and `sensor.wallboard_unlocked_entries`.
- **Appliance Running**: `binary_sensor.wallboard_washer_active`, `binary_sensor.wallboard_dryer_active` and `binary_sensor.wallboard_dishwasher_active`.

The package uses the same overlay as the Display for the entry locks, entry labels and appliance power sensors. Nothing outside the Wallboard may read those Wallboard-local entities (ADR 0010).

### Prerequisites

- Calendars for family events, appointments, trips/breaks, birthdays, US holidays, school closures and Collection Day.
- A weather entity for the Household Schedule forecast.
- To-do lists for Shopping Items, Reminders and After-School Tasks, shown on the rail with the built-in to-do list card.
- The School Day helper: an `input_select` with the options `true` and `false`. On a School Day afternoon (from 12:00), After-School Tasks take the place of Family Reminders on the rail.
- Entry locks and appliance power sensors named in the local overlay, for the Wallboard support package above.
- Custom cards listed in `custom-cards.yaml`: week-planner-card, card-mod and kiosk-mode.
- The Wallboard base theme in `wallboard/themes/wallboard.yaml`, selected in the kiosk browser's profile.

### Install

1. Copy the rendered `wallboard/build/package.yaml` into Home Assistant's packages, then restart or reload template entities.
2. Check that the Wallboard-local role entities exist and update: `binary_sensor.wallboard_any_entry_unlocked`, `sensor.wallboard_unlocked_entries`, `binary_sensor.wallboard_washer_active`, `binary_sensor.wallboard_dryer_active` and `binary_sensor.wallboard_dishwasher_active`.
3. Paste the rendered `wallboard/build/wallboard.yaml` into the raw configuration editor of a new storage dashboard, alongside the current Wallboard. Do not paste over the current Wallboard until cutover.
4. Copy `wallboard/themes/wallboard.yaml` into Home Assistant's themes folder, run **Reload themes**, then select the **Wallboard** theme in the kiosk browser's profile. The view pins no theme (ADR 0011). This base theme only widens the sections view columns so the three columns fill a 1920px screen; everything else keeps Home Assistant's defaults. Seasonal Look themes will build on it.
5. Verify the Wallboard opens without the header or sidebar and shows the glance band, Household Schedule and rail across one 1080p screen without scrolling.
6. On the rail, add an item through each list's add field with the touch keyboard, and check that it appears in the matching to-do list.

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
