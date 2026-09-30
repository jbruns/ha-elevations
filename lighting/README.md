# Lighting

Turns lights on and off with the sun, the hour, and each other. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Follow blueprint.
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

## Cutover

Back up Home Assistant first, including `automations.yaml`.

1. Import `lighting_follow.yaml` from GitHub.
2. Create one Follow automation for the cabinet light that follows a switch, with Copy brightness disabled.
3. Create one Follow automation for the cabinet light that follows an overhead light, with Copy brightness enabled.
4. Disable the three old `[Lighting]` automations being replaced: the cabinet-light on automation, the cabinet-light off automation, and the overhead-to-cabinet sync automation.
5. Test each Leader at the wall and in Home Assistant, including dimming the overhead light.
6. After the replacement instances work, delete the three old automations.
