# IoX manages only the Insteon network; Home Assistant does every automation

The eisy (IoX) keeps the Insteon Devices, the Modem, and Scenes. Scenes are Links stored on the Devices themselves, so a keypad still drives its lights when Home Assistant or the eisy is down. Everything conditional, timed, remembered or monitored lives in Home Assistant: IoX programs, variables, notifications and schedules move to Home Assistant and are deleted from IoX. The aim is to give IoX as little as possible to do, so it is always available to manage the Insteon network.

## Considered Options

- **Radio only** (no Scenes on IoX): every button press would go through Home Assistant. Rejected because wall controls would stop working when Home Assistant is down and would respond more slowly.
- **Keep a few IoX programs as a safety net**: rejected because logic split across two systems is how lights end up controlled from both sides.

## Consequences

- Keeping keypad LEDs in step with a light changed from Home Assistant is a Lighting automation (Keypad Sync), not an IoX program; IoX only relays the Scene command.

- An IoX program is a migration target, never a place to add behaviour.
- Z-Wave or Zigbee nodes on the eisy are a misconfiguration; those networks belong to Home Assistant's own integrations.
