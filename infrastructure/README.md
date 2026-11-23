# Infrastructure

Tells the Administrator when a system the home depends on fails or recovers, which batteries need replacing, and which Insteon Devices are Unresponsive Devices, to Remove or Restore. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Failure Notifications, Battery Digest and Unresponsive Devices Digest blueprints.
- `tests/`: runs the blueprints in a real Home Assistant core.

Infrastructure Notifications go only to the Administrator, never to every Recipient (ADR 0006). Every entity and every number is an input (ADR 0004).

## Failure Notifications

`blueprints/automation/infrastructure_failure_notifications.yaml` watches one Monitored System. Create one automation from it for each Monitored System.

- **Failed test**: an entity is failing when it is:
  - *unavailable or unknown*;
  - *not in the expected states*, such as `on`, `ready` or `OL`. Any other state counts, including `unavailable`;
  - *below* or *above* a limit, in the entity's own unit. A value at the limit is healthy. A state that isn't a number, such as `unavailable`, neither starts a Failure nor ends one: after a Failure, the Recovery waits for a number within the limit. To hear about a sensor dropping out, add an *unavailable* instance for it.
- **Any or all**: whether one failing entity makes a Failure, or only all of them at once, for example "all three probe lights unavailable".
- **Failure**: once the test has held for the grace period, 5 minutes by default, the Administrator gets **\<System\> failed**. It names each failing entity and its state, and for numeric tests the limit, for example *Core UPS Battery Charge is 24 %, below 30 %.* A test that stops holding before the grace period ends sends nothing.
- **Recovery**: when the test stops holding after a Failure, **\<System\> recovered** replaces the Failure Notification in place, silently. With *all*, one entity back is a Recovery. There is no Recovery without a Failure.
- **Starting Home Assistant**: a condition that was already true when Home Assistant started, or when the automation was created or reloaded, sends nothing. A Failure under way when Home Assistant restarts, or automations reload, gets no Recovery; the Failure Notification stays on the phone until the next Failure of that system replaces it.

### Set up

1. Check that the Administrator's phone has the Home Assistant Companion app, so it is a `mobile_app` device.
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create an automation from it for each Monitored System. Give it the system's name, its entities, the failed test (with its expected states or limit), any or all, the grace period and the Administrator.
4. Turn off any other automation that notifies about the same system.

For example:

| Monitored System | Entities | Failed test | Any / all | Grace period |
| --- | --- | --- | --- | --- |
| Insteon network | its probe lights | unavailable | all | 5 min |
| Zigbee network | the Zigbee bridge's connection state | not `on` | any | 5 min |
| Z-Wave network | the Z-Wave controller's status | not `ready` | any | 5 min |
| Front-door camera | the camera | unavailable | any | 5 min |
| Core UPS | its status | not `OL` | any | 2 min |
| Core UPS battery | its battery charge | below 30 | any | 5 min |
| Host disk | its free space, in GiB | below 100 | any | 10 min |
| Rack temperature | the rack's temperature sensor | above 90 | any | 5 min |

### Check it works

- Pick a system you can safely break, such as a probe light: unplug it. After the grace period, **\<System\> failed** arrives.
- Plug it back in. **\<System\> recovered** replaces it without a sound.
- The automation's traces show each run. A run waiting at the `wait_template` is a Failure waiting for its Recovery.

## Battery Digest

`blueprints/automation/infrastructure_battery_digest.yaml` sends the Administrator one **Battery Digest** a day, at 09:00 by default. Create a single automation from it.

- **What it lists**, sorted by name, one per line:
  - battery level sensors (`sensor`, device class `battery`) below the low threshold, 25 % by default, for example *Front Door Lock Battery (15%)*. A level at the threshold is not low;
  - battery binary sensors (`binary_sensor`, device class `battery`) that are on, as *Leak Sensor Battery (low)*;
  - included entities that are on, also as *(low)*. Use these for binary sensors that mean "low battery" without the battery device class, such as a smoke alarm bridge's low-battery sensor. They are listed only when on: some have no state until their device sends one, so being `unavailable` or `unknown` says nothing about the battery;
  - battery sensors and battery binary sensors that have been `unavailable` or `unknown` for longer than *unavailable after*, 24 hours by default, as *Front Door Lock Battery (unavailable)*. One that dropped out more recently is left out. Home Assistant restarting starts the count again.
- **Exclusions**: batteries from the excluded integrations, `mobile_app` (phones) and `nut` (UPSes) by default, and the excluded entities are never listed. Give an integration by its domain, as in `integration_entities()`.
- **Nothing to report**: when the list is empty, nothing is sent.

### Set up

1. Check that the Administrator's phone has the Home Assistant Companion app, so it is a `mobile_app` device.
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create one automation from it. Set the Administrator, and change the time of day, low threshold, unavailable after, excluded integrations, excluded entities and included entities if the defaults don't suit.
4. Turn off any other low-battery automation, and remove the template sensor and threshold helper it used.

### Check it works

- In Developer Tools → Template, paste `{{ states.sensor | selectattr('attributes.device_class', 'eq', 'battery') | map(attribute='name') | list }}` to see the battery sensors it can list.
- Raise the low threshold above one battery's level and run the automation from its menu (Run actions). The Administrator gets **Battery Digest** listing it. Set the threshold back.
- The automation's traces show each day's run. A run stopped at the condition had nothing to report.

## Unresponsive Devices Digest

`blueprints/automation/infrastructure_unresponsive_devices_digest.yaml` sends the Administrator one **Unresponsive Devices Digest** a day, at 09:00 by default. Create a single automation from it.

- **What it lists**: each [Unresponsive Device](../insteon-network/CONTEXT.md), an Insteon Device whose communication-error sensor reports errors, sorted, one per line, for example *Example Dimmer*. It uses the Device's name on the Controller, not a name given it in Home Assistant. Only the Administrator decides whether to Remove or Restore it.
- **Which sensors**: every `isy994` sensor whose entity ID ends in `_device_communication_errors`, the ID the integration gives each Device's *Device Communication Errors* sensor. It reads `0` while the Device answers and `1` once the Controller can't reach it. A sensor that is `unknown`, because the Controller hasn't reported on it since Home Assistant started, or `unavailable`, because the Device is disabled on the Controller, is not listed. A renamed sensor whose ID no longer ends that way is not found.
- **Exclusions**: the excluded entities are never listed.
- **Nothing to report**: when the list is empty, nothing is sent.

### Set up

1. Enable the communication-error sensors. Home Assistant creates one per Insteon Device but disables them. Go to Settings → Devices & Services → Entities, search for `device_communication_errors`, select them all, and choose **Enable selected**. Home Assistant reloads the integration about 30 seconds later.
2. Check that the Administrator's phone has the Home Assistant Companion app, so it is a `mobile_app` device.
3. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
4. Create one automation from it. Set the Administrator, and change the time of day and excluded entities if the defaults don't suit.

When a Device is added, enable its new communication-error sensor the same way.

### Check it works

- In Developer Tools → Template, paste `{{ integration_entities('isy994') | select('match', 'sensor[.].*_device_communication_errors$') | map('states') | list }}` to see the sensors it reads and their states.
- Unplug a plug-in Device, or switch off a wired one at its breaker, and turn it on and off from Home Assistant until its sensor reads `1`. Run the automation from its menu (Run actions). The Administrator gets **Unresponsive Devices Digest** listing it. Restore power and query the Device (its *Query* button) so the sensor reads `0` again.
- The automation's traces show each day's run. A run stopped at the condition had nothing to report.
