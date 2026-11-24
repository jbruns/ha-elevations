# Lighting

Turns lights on and off with the sun, the hour, and each other. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Follow, Exterior Lights, Night Timeout, and Keypad Sync blueprints.
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

## Keypad Sync

`blueprints/automation/lighting_keypad_sync.yaml` keeps a light's keypad buttons lit to match the light (**Keypad Sync**). It replaces the IoX "Sync" program for that light (ADR 0012).

Each Insteon light has a cross-linked room Scene, so a press at the wall already sets the light and every keypad button for it. A change from Home Assistant commands the light alone, so its buttons stay as they were. The light's Keypad Sync Scene holds only those buttons; isy994 shows it as a `switch`.

- When the light turns on or off, the blueprint waits for the **Settle time** (2 seconds by default), then checks the Scene.
- If the light is on and not every button is lit, it turns the Scene on. If the light is off and any button is lit, it turns the Scene off.
- If the buttons already match, it sends nothing. So a press at the wall, which lit them through the room Scene, costs no extra Insteon traffic, whatever the change's `context` says.
- If the light changes again during the Settle time, the wait starts over, and only the light's last state counts.
- Changes from or to `unavailable` or `unknown` are ignored, so restarts and reloads don't switch the Scene.
- The Scene holds only keypad buttons, not the light, and the blueprint never triggers on the Scene, so switching it can't feed back into the light.

It runs only when the light changes. Unlike the IoX program, it doesn't correct a keypad button that changes on its own while the light stays the same.

It reads isy994's `group_all_on` attribute to tell "every button lit" from "some buttons lit".

### Set up

1. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
2. Create one automation from it for each light that has a Keypad Sync Scene.
3. Pick the Light and its Keypad Sync Scene switch. Keep the default Settle time unless a press at the wall takes longer to light the buttons.
4. Once it works, turn off the IoX Sync program for the same light. Until then the two run side by side, which is harmless but doubles the Scene commands.

### Check it works

- Turn the light on from Home Assistant. About 2 seconds later its keypad buttons should light, and the trace should show `switch.turn_on` on the Scene.
- Turn it off from Home Assistant. The buttons should go dark.
- Press the light's button at the wall. The buttons change with it, and the trace should show no action after the Settle time.
