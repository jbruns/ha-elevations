# Climate

Keeps the home comfortable through its thermostat. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Comfort Policy blueprint.
- `tests/`: runs the blueprints in a real Home Assistant core.

Home Assistant imports these blueprints by URL, so they take every entity and every policy number as an input (ADR 0004). They depend on no template sensors: Household Home comes from `zone.home` and the forecast from `weather.get_forecasts` (ADR 0005).

**Numbers**: every number below is a default, and each one is an input.

**Units**: every temperature input is in the thermostat's own unit. The defaults assume °F. If your thermostat uses °C, change every temperature input.

## Comfort Policy

`blueprints/automation/climate_comfort_policy.yaml` is the whole Comfort Policy in one automation. It acts only when the thermostat is in `heat`, `cool` or `heat_cool`. It never changes an `off` thermostat, and makes no changes during a Door Pause.

### Comfort Targets

The Comfort Target is worked out in one place, each time the policy acts:

- **Sleeping** means the time is at or after Sleep start, or before Wake time. Sleep targets are 65 heating / 67 cooling.
- **Daytime** targets are 66 heating / 76 cooling, adjusted for the forecast:
  - The forecast low and high are the lowest and highest of the hourly forecast's temperatures over the next 12 hours and the current outdoor temperature.
  - Heating +2 when the forecast low is at or below 40, or +4 at or below 20.
  - Cooling −2 when the forecast high is at or above 80, or −4 at or above 90.
- **Humid**: the cooling target is at most 74. The air turns humid once indoor humidity has stayed at or above 60 for 15 minutes, and stays humid until it has stayed below 55 for 15 minutes.
- **Dry**: the heating target is 1 lower, but not below 65. The air turns dry once humidity has stayed below 35 for 30 minutes, and stays dry until it has stayed above 38 for 30 minutes.

The policy records humid or dry in a Dropdown helper, so the state holds between the thresholds, and across restarts. It keeps the helper up to date even while the household is away or the thermostat is off.

In `heat` the policy sets the heating target, and in `cool` the cooling target. In `heat_cool` it sets both, with the cooling one at least 3 above the heating one.

It applies the Comfort Target at Sleep start and Wake time, when Home Assistant starts, when Household Home turns on, and when a Door Pause ends while someone is home. It also applies it whenever the air turns humid or dry, or stops being so.

### Setback

When Household Home has been off for 30 minutes, the policy sets the Setback: 60 heating / 74 cooling. If Home Assistant starts while the household is away, the Setback applies at once. When someone comes home, the Comfort Target returns. While the household is away, nothing else changes the thermostat.

### Comfort Band

While someone is home, every 15 minutes and whenever the thermostat's settings change, the policy checks each setting against the Comfort Band. The Band runs from 2 below the heating target to 3 above the cooling target. A setting outside the Band is pulled back to its nearest edge. In `heat_cool`, the 3° gap is restored too. A setting inside the Band is never touched, so a reasonable manual change stays until the Comfort Target is next applied.

The Band replaces the previous automation's separate range table: now only the cold moves its bottom edge and only the heat its top edge. `tests/test_comfort_policy.py` compares the two for representative forecasts.

### Set up

1. Create the helpers, in Settings → Devices & Services → Helpers → Create Helper:
   - Two Date and/or time helpers, **time only**: Sleep start and Wake time.
   - One Dropdown helper for the humidity, with exactly the options `normal`, `humid` and `dry`. Only the policy changes it.
   - One Toggle helper for the Door Pause, if you don't have one yet. The Door Pause blueprint turns it on and off; share the same helper.
2. Check that each household member is a person with a device tracker, so `zone.home` counts who is home.
3. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
4. Create an automation from it. Pick the thermostat, the indoor humidity and outdoor temperature sensors, a weather entity with an hourly forecast, and the helpers. Change any number under Comfort Targets, Humidity, Setback or Comfort Band to suit your home.
5. Turn off any other automation that sets the thermostat's temperatures, so they don't fight.

### Check it works

- At Wake time, the thermostat's setting changes to the daytime Comfort Target.
- Set the thermostat a little warmer by hand. It stays. Set it far outside the Band; within moments it returns to the Band's edge.
- Leave home. After 30 minutes the Setback applies, and it lifts when you return.
- The automation's traces show each run. A run that stopped at a condition changed nothing, for example because the thermostat was off or a Door Pause was on.
