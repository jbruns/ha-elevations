# Lighting

Turns lights on and off with the sun, the hour, and each other. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Follow, Exterior Lights, and Night Timeout blueprints.
- `tests/`: runs the blueprints in a real Home Assistant core.

Home Assistant imports these blueprints by URL, so they take every entity and every policy number as an input (ADR 0004). They use entity triggers rather than device triggers, so replacing a device does not silently break an automation.

## Follow

`blueprints/automation/lighting_follow.yaml` makes a **Follower** light copy a **Leader** light or switch.

- When the Leader turns on, the Follower turns on.
- When the Leader turns off, the Follower turns off.
- When **Copy brightness** is enabled and the Leader is a light with brightness, the Follower turns on at the Leader's brightness and follows later brightness changes.
- When **Copy brightness** is disabled, brightness changes still turn the Follower on, but no brightness value is sent.
- A switch Leader never sends brightness.

### Set up

1. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
2. Create one automation from it for each Leader/Follower pair.
3. Pick the Leader light or switch, the Follower light, and whether to copy brightness.
4. Turn off any old automation that controls the same Follower from the same Leader, so they do not fight.

### Check it works

- Turn the Leader on and off. The Follower should turn on and off with it.
- If Copy brightness is enabled, dim the Leader. The Follower should receive the same brightness.
- If Copy brightness is disabled, dimming the Leader should not send a brightness value to the Follower.
- The automation's traces show each run and the entity trigger that started it.

## Exterior Lights

`blueprints/automation/lighting_exterior_lights.yaml` keeps the **Exterior Lights** on from dusk to dawn, judged by `sun.sun` elevation.

- When elevation falls below **Dusk elevation**, the targets turn on. The default is `1.5°`.
- When elevation rises above **Dawn elevation**, the targets turn off. The default is `1°`.
- Targets may be lights or switches, and are called through `homeassistant.turn_on` and `homeassistant.turn_off`.
- If Home Assistant starts at night, below the Dawn elevation, the blueprint turns the targets on. If it starts in daytime, above the Dusk elevation, it turns them off. Elevations between the two thresholds do nothing because that twilight band is intentionally ambiguous after a restart.

### Set up

1. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
2. Create one automation from it for the Exterior Lights.
3. Pick every Exterior Light target, including any switches that power exterior lighting.
4. Keep the default Dusk elevation and Dawn elevation unless the lights need to switch at a different sun height.
5. Turn off any old dusk or dawn automation that controls the same targets, so they do not fight.

### Check it works

- At dusk, when `sun.sun` elevation crosses below the Dusk elevation, all targets should turn on.
- At dawn, when `sun.sun` elevation crosses above the Dawn elevation, all targets should turn off.
- During the twilight band between the thresholds, no extra action should run.
- Restart Home Assistant after dark if practical; the automation should turn the targets on after startup.

## Night Timeout

`blueprints/automation/lighting_night_timeout.yaml` turns off a light that has been left on too long during night hours.

- The Light is selected by entity, not device.
- Duration defaults to 30 minutes.
- Night hours default to 22:00 through 07:00 and may cross midnight.
- If a light turns on before night hours and reaches the Duration during night hours, it turns off then.
- If the light has already been on for at least the Duration when night hours start, it turns off at night start.

### Set up

1. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
2. Create one automation from it for each light that needs a Night Timeout.
3. Pick the Light, Duration, Night hours start, and Night hours end.
4. Turn off any old automation that controls the same light after the same nighttime timeout, so they do not fight.

### Check it works

- Turn the light on during night hours and leave it on for the Duration; it should turn off.
- Turn the light on during the day and leave it on for the Duration; it should stay on.
- Turn the light off before the Duration; the automation should not call it again.
- For night hours that cross midnight, test both before and after midnight if practical.
