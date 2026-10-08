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

`wallboard/display.yaml` renders the Wallboard from one sections view and one file per section. The view has a full-width glance band above the Household Schedule and a rail beside it. Tapping the Household Schedule heading opens the Month subview (`wallboard/month.yaml`): the Household Schedule calendars in Home Assistant's built-in calendar card as a month grid, and the Wallboard's only secondary view. Its heading leads back, since kiosk-mode hides the header's back arrow.

The Wallboard never creates events: the Month calendar card shows no add-event button. Tapping an event in Month opens its details, and for calendars that support deleting events, such as Local Calendar, that dialog still offers Delete. Month does not merge identical events, so a calendar that lists the same event twice, such as two `Recycle` events on one Collection Day, shows it twice there; the week planner shows it once. Month colours each calendar from its entity settings (**Settings → Entities → calendar → Color**), not from the week-planner-card colours; set them to match if wanted. Copy `wallboard/wallboard.local.example.yaml` to `wallboard/wallboard.local.yaml`, fill in this home's real values, then render with:

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

`wallboard/package.yaml` is optional support for Wallboard-local roles. The rail shows Countdowns and school lunch; the Wallboard does not show the other roles yet:

- **Unlocked Door**: `binary_sensor.wallboard_unlocked_door`, on while any door is an Unlocked Door, and `sensor.wallboard_unlocked_doors`, whose state names them, such as `Front Door, Side Door`. Household Home comes from `zone.home`, as in Climate (ADR 0005), and sunset to sunrise from `sun.sun`. The overlay names the door into the garage, which counts only while the garage door cover is open.
- **Appliance running**: `binary_sensor.wallboard_washer_active`, `binary_sensor.wallboard_dryer_active` and `binary_sensor.wallboard_dishwasher_active`. A running appliance is not an Attention Item.
- **Finished Cycle**: `binary_sensor.wallboard_washer_finished_cycle` and `binary_sensor.wallboard_dryer_finished_cycle`. Each turns on when its appliance's running sensor turns off, after that sensor's off delay, and turns off when acknowledged, when the next load starts or after 3 hours. It survives a restart, even one in the middle of a load. The dishwasher has none.
- **Collection Day**: `binary_sensor.wallboard_bins_out`, on from 17:00 the evening before a Collection Day until midnight. Its `bins` attribute names the bins once each, such as `Recycle + Solid Waste`, from the Collection calendar's all-day events.
- **Countdown**: `sensor.wallboard_countdowns`, whose `countdowns` attribute lists up to four, soonest first, each with `title`, `date` and `days`. It counts trips and breaks and the holidays listed in the overlay's `COUNTDOWN_HOLIDAYS` within 90 days, and birthdays within 30. A Countdown drops off once its event starts.
- **School lunch**: `sensor.wallboard_school_lunch`, whose `today` and `tomorrow` attributes name that day's school lunch from the lunch calendar's all-day events, without a leading `Lunch:`. Either is empty on a day without one.
- **Acknowledge**: a card taps `script.wallboard_acknowledge` with `attention_item` set to a Finished Cycle or `binary_sensor.wallboard_bins_out` to clear it. Bins out stays cleared until the next Collection Day's evening.

The package uses the same overlay as the Display. Nothing outside the Wallboard may read those Wallboard-local entities (ADR 0010).

### Today and Countdowns

The rail opens with the Today card. On a School Day it shows a School Day banner, each child's Special Class and today's school lunch; from 15:00 it adds tomorrow's lunch when tomorrow is a School Day too. A child whose helper says `No class` or `Not set` is left out. On any other day it shows tomorrow's lunch under a "School Day tomorrow" banner when tomorrow is a School Day, and hides when it is not, so Countdowns move up. Family publishes only today's Special Class, so the card has none for tomorrow. The card also hides until 06:15, after the School Day and Special Classes blueprints update their helpers at their default times; if those run later, change the card's `after` time to match.

Countdowns follow: up to four from `sensor.wallboard_countdowns`, soonest first, each with the days left, or Today or Tomorrow. The card hides when there are none.

### Chores

The rail shows each child's Chores from ChoreOps: overdue Chores first, then those due today, up to six with "+N more" after. Tapping a Chore presses its ChoreOps Claim button, with no confirmation. A claimed Chore stays greyed and can't be tapped until a parent approves it; approvals stay on the ChoreOps Chores dashboard. Claimed Chores sit after the open ones, so a Chore moves to the end once claimed. The overlay names each child and their ChoreOps dashboard helper sensor, `sensor.<child>_choreops_ui_dashboard_helper`.

ChoreOps refuses a Claim from a Home Assistant user it does not authorize for that child. Either enable ChoreOps kiosk mode, or sign the kiosk browser in as a user ChoreOps authorizes.

### Prerequisites

- Calendars for family events, appointments, trips/breaks, birthdays, US holidays, school closures and Collection Day.
- A weather entity for the Household Schedule forecast.
- To-do lists for Shopping Items, Reminders and After-School Tasks, shown on the rail with the built-in to-do list card.
- Family's School Day helpers for today and tomorrow, the Toggle helpers kept up to date by the School Day blueprint. On a School Day afternoon (from 12:00), After-School Tasks take the place of Family Reminders on the rail.
- Family's Special Class helper for each child, kept up to date by the Special Classes blueprint, and a school lunch calendar with one all-day event per School Day, for the Today card.
- Door locks, the garage door cover and appliance power sensors named in the local overlay, plus `sun.sun` and `zone.home`, for the Wallboard support package above.
- ChoreOps, with a dashboard helper sensor for each child named in the local overlay.
- Custom cards listed in `custom-cards.yaml`: week-planner-card, auto-entities, Mushroom, card-mod and kiosk-mode.
- The Wallboard base theme in `wallboard/themes/wallboard.yaml`, selected in the kiosk browser's profile.

### Install

1. Copy the rendered `wallboard/build/package.yaml` into Home Assistant's packages, then restart Home Assistant.
2. Check that the Wallboard-local role entities listed above exist and update.
3. Paste the rendered `wallboard/build/wallboard.yaml` into the raw configuration editor of a new storage dashboard, alongside the current Wallboard. Do not paste over the current Wallboard until cutover.
4. Copy `wallboard/themes/wallboard.yaml` into Home Assistant's themes folder, run **Reload themes**, then select the **Wallboard** theme in the kiosk browser's profile. The view pins no theme (ADR 0011). This base theme only widens the sections view columns so the three columns fill a 1920px screen; everything else keeps Home Assistant's defaults. Seasonal Look themes will build on it.
5. Verify the Wallboard opens without the header or sidebar and shows the glance band, Household Schedule and rail across one 1080p screen without scrolling. Tap the Household Schedule heading: Month opens as a month grid without scrolling, and its heading returns to the main view.
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
