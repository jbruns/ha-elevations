# Safety

Warns every Recipient about hazards in the home, and about readings that have stayed outside safe limits. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Hazard Notifications and Limit Breach Notifications blueprints.
- `tests/`: runs the blueprints in a real Home Assistant core.

Every entity and every number is an input (ADR 0004).

## Hazard Notifications

`blueprints/automation/safety_hazard_notifications.yaml` watches one binary sensor for one Hazard, such as a freezer door left open, or the Immediate Hazards role for smoke, carbon monoxide and leaks. Create one automation from it for each non-immediate Hazard, and one automation from it for the Immediate Hazards role.

- **Hazard when the sensor is**: `on` by default. Some integrations report a contact the other way round, such as a freezer door sensor that is `on` when the door is closed: for those, choose `off`.
- **Hazard**: once the sensor has been in the Hazard state for *held for*, every Recipient gets the Notification with its title and message, for example **Water leak**: *Leak at the kitchen sink*. *Held for* is 0 by default, so the warning is immediate; a sensor that leaves the Hazard state sooner sends nothing. The sensor changing into the Hazard state from `unavailable` or `unknown` is a Hazard too, even during a Hazard: it warns again, with the same sound. When the sensor is the Immediate Hazards role, the Notification message names the member sensor that reported.
- **Critical**: on by default. The Hazard plays an iOS critical sound, at full volume even in silent mode and through Focus modes. Turned off, it plays the normal sound.
- **Cleared**: when the sensor reports the opposite state after a Hazard, **\<Title\> cleared**, with the same message, replaces the Hazard's Notification in place, silently. The sensor dropping out to `unavailable` or `unknown` doesn't clear the Hazard. There is no clear without a Hazard.
- **Starting Home Assistant**: a sensor already in the Hazard state when Home Assistant starts, or when the automation is created or reloaded, sends nothing, unless it came back from `unavailable` or `unknown`. A Hazard under way when Home Assistant restarts, or automations reload, gets no cleared Notification; the Hazard Notification stays on the phones until the next Hazard from that automation replaces it.

### Immediate Hazards role

Create one binary sensor group helper named **Immediate Hazards** for the Immediate Hazards. In Home Assistant, go to Settings → Devices & Services → Helpers → Create Helper → Group → Binary sensor group. Add the smoke, carbon monoxide and leak sensors that are dangerous as soon as they report. Each member must read `on` while hazardous. Hazard Notifications can use this one role, so adding a new leak, smoke or carbon monoxide sensor means editing the helper only.

### Set up

1. Check that every Recipient's phone has the Home Assistant Companion app, so it is a `mobile_app` device. For critical sounds, allow Critical Alerts for the app on each iPhone: Settings → Notifications → Home Assistant.
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create one automation from it for the Immediate Hazards role. Give it the role sensor, `on`, held for 0, a title and message, critical on, and the Recipients.
4. Create an automation from it for each non-immediate Hazard, such as a freezer door left open. Give it the sensor, whether the Hazard is `on` or `off`, how long it must be held, the title and message, whether it is critical, and the Recipients.
5. Turn off any other automation that warns about the same sensor.

For example:

| Hazard | Hazard when | Held for | Title / message | Critical |
| --- | --- | --- | --- | --- |
| Immediate Hazards role | `on` | 0 | Immediate hazard / Hazard reported | yes |
| Freezer door | `off` (sensor is `on` when closed) | 10 min | Freezer door open / The freezer door has been open for 10 minutes | no |

### Check it works

- Wet a leak sensor, or open the freezer door for longer than *held for*. The Hazard arrives on every Recipient's phone, with a critical sound if set.
- Dry the sensor, or close the door. **\<Title\> cleared** replaces it without a sound.
- The automation's traces show each run. A run waiting at the `wait_template` is a Hazard waiting to clear.

## Limit Breach Notifications

`blueprints/automation/safety_limit_breach_notifications.yaml` watches one numeric sensor, such as a freezer's temperature or the outdoor air quality. Create one automation from it for each reading. It is separate from Infrastructure's Failure Notifications, which only ever tell the Administrator (ADR 0006).

- **Breach when the reading is**: *above* the limit by default, or *below* it. The limit is in the sensor's own unit. A reading at the limit is within it.
- **Limit Breach**: once the reading has been past the limit for the *duration*, every Recipient gets the Notification with its title and message, followed by the current reading, for example **Freezer too warm**: *The chest freezer is above 15 °F. Now 18 °F.* The duration is 0 by default, so the warning is immediate; a reading back within the limit sooner sends nothing. A reading moving further past the limit is the same Limit Breach, and sends nothing more.
- **Critical**: off by default, so the Breach plays the normal sound. Turned on, it plays an iOS critical sound, at full volume even in silent mode and through Focus modes.
- **Cleared**: when the reading comes back within the limit after a Breach, **\<Title\> cleared**, with the same message and the current reading, replaces the Breach's Notification in place, silently. There is no clear without a Breach.
- **Unavailable readings**: a state that isn't a number, such as `unavailable` or `unknown`, neither breaches nor clears. A reading coming back past the limit from one is a Breach, even during a Breach: it warns again.
- **Starting Home Assistant**: a reading already past the limit when Home Assistant starts, or when the automation is created or reloaded, sends nothing, unless it came back from `unavailable` or `unknown`. A Breach under way when Home Assistant restarts, or automations reload, gets no cleared Notification; the Breach Notification stays on the phones until the next Breach from that automation replaces it.

### Set up

1. Check that every Recipient's phone has the Home Assistant Companion app, so it is a `mobile_app` device. For critical sounds, allow Critical Alerts for the app on each iPhone: Settings → Notifications → Home Assistant.
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create an automation from it for each reading. Give it the sensor, above or below, the limit, the duration, the title and message, whether it is critical, and the Recipients.
4. Turn off any other automation that warns about the same reading.

For example:

| Reading | Breach when | Limit | Duration | Title / message | Critical |
| --- | --- | --- | --- | --- | --- |
| Freezer temperature | above | 15 | 0 | Freezer too warm / The chest freezer is above 15 °F. | no |
| Outdoor air quality | above | 100 | 30 min | Poor air quality / Outdoor AQI has been above 100 for 30 minutes. | no |

### Check it works

- Pick a limit the reading is just inside, such as a few degrees above the freezer's temperature, and wait for it to cross. The Breach arrives on every Recipient's phone, with the current reading.
- When the reading comes back within the limit, **\<Title\> cleared** replaces it without a sound. Then set the limit back.
- The automation's traces show each run. A run waiting at the `wait_template` is a Limit Breach waiting to clear.
