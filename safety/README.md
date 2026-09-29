# Safety

Warns every Recipient about hazards in the home. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Hazard Notifications blueprint.
- `tests/`: runs the blueprints in a real Home Assistant core.

Every entity and every number is an input (ADR 0004).

## Hazard Notifications

`blueprints/automation/safety_hazard_notifications.yaml` watches one binary sensor for one Hazard, such as a water leak, smoke, carbon monoxide or a freezer door left open. Create one automation from it for each Hazard.

- **Hazard when the sensor is**: `on` by default. Some integrations report a contact the other way round, such as a freezer door sensor that is `on` when the door is closed: for those, choose `off`.
- **Hazard**: once the sensor has been in the Hazard state for *held for*, every Recipient gets the Notification with its title and message, for example **Water leak**: *Leak at the kitchen sink*. *Held for* is 0 by default, so the warning is immediate; a sensor that leaves the Hazard state sooner sends nothing. The sensor changing into the Hazard state from `unavailable` or `unknown` is a Hazard too, even during a Hazard: it warns again, with the same sound.
- **Critical**: on by default. The Hazard plays an iOS critical sound, at full volume even in silent mode and through Focus modes. Turned off, it plays the normal sound.
- **Cleared**: when the sensor reports the opposite state after a Hazard, **\<Title\> cleared**, with the same message, replaces the Hazard's Notification in place, silently. The sensor dropping out to `unavailable` or `unknown` doesn't clear the Hazard. There is no clear without a Hazard.
- **Starting Home Assistant**: a sensor already in the Hazard state when Home Assistant starts, or when the automation is created or reloaded, sends nothing, unless it came back from `unavailable` or `unknown`. A Hazard under way when Home Assistant restarts, or automations reload, gets no cleared Notification; the Hazard Notification stays on the phones until the next Hazard from that automation replaces it.

### Set up

1. Check that every Recipient's phone has the Home Assistant Companion app, so it is a `mobile_app` device. For critical sounds, allow Critical Alerts for the app on each iPhone: Settings → Notifications → Home Assistant.
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create an automation from it for each Hazard. Give it the sensor, whether the Hazard is `on` or `off`, how long it must be held, the title and message, whether it is critical, and the Recipients.
4. Turn off any other automation that warns about the same sensor.

For example:

| Hazard | Hazard when | Held for | Title / message | Critical |
| --- | --- | --- | --- | --- |
| Kitchen sink leak | `on` | 0 | Water leak / Leak at the kitchen sink | yes |
| Water heater leak | `on` | 0 | Water leak / Leak at the water heater | yes |
| Smoke | `on` | 0 | Smoke / Smoke detected | yes |
| Carbon monoxide | `on` | 0 | Carbon monoxide / Carbon monoxide detected | yes |
| Freezer door | `off` (sensor is `on` when closed) | 10 min | Freezer door open / The freezer door has been open for 10 minutes | no |

### Check it works

- Wet a leak sensor, or open the freezer door for longer than *held for*. The Hazard arrives on every Recipient's phone, with a critical sound if set.
- Dry the sensor, or close the door. **\<Title\> cleared** replaces it without a sound.
- The automation's traces show each run. A run waiting at the `wait_template` is a Hazard waiting to clear.
