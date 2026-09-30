# Lighting

Turns lights on and off with the sun, the hour, and each other. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Follow and Exterior Lights blueprints.
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

- When elevation falls below **Dusk elevation**, the targets turn on. The default is `1.5°`, matching the replaced live automation.
- When elevation rises above **Dawn elevation**, the targets turn off. The default is `1°`, matching the replaced live automation.
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
- Restart Home Assistant after dark during cutover if practical; the automation should turn the targets on after startup.

## Cutover

Back up Home Assistant first, including `automations.yaml`.

### Follow

1. Import `lighting_follow.yaml` from GitHub.
2. Create one Follow automation for the cabinet light that follows a switch, with Copy brightness disabled.
3. Create one Follow automation for the cabinet light that follows an overhead light, with Copy brightness enabled.
4. Disable the three old `[Lighting]` automations being replaced: the cabinet-light on automation, the cabinet-light off automation, and the overhead-to-cabinet sync automation.
5. Test each Leader at the wall and in Home Assistant, including dimming the overhead light.
6. After the replacement instances work, delete the three old automations.

### Exterior Lights

1. Import `lighting_exterior_lights.yaml` from GitHub.
2. Create one Exterior Lights automation with the same light and switch targets as the old dusk and dawn automations.
3. Leave Dusk elevation at `1.5°` and Dawn elevation at `1°` unless deliberately changing the policy.
4. Disable the old `[Lighting]` dusk and dawn automations.
5. Test the new automation by temporarily adjusting thresholds around the current `sun.sun` elevation, or by waiting for the next dusk and dawn.
6. After the replacement instance works, delete the two old automations.
