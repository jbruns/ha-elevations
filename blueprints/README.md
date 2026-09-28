# Blueprints

Home Assistant imports these blueprints by URL, so they take every camera, phone and URL as an input (ADR 0004).

## Alert Notifications

`automation/jbruns/alert_notifications.yaml` sends one Notification per Alert on a Frigate camera to each Recipient.

- The title is the camera's name, for example "Front Door". The body names each moving Tracked Object and its Watched Zone, for example "Person in Entry Breezeway" or "Car arriving in Driveway". A recognized face or plate sub-label replaces the label.
- The image is the moving Tracked Object's cropped snapshot with a bounding box, never the Review preview GIF (ADR 0003).
- Only the first delivery makes a sound. Later updates replace it silently, each with a new image URL: when objects join, when Frigate's snapshot improves, and with the final best snapshot when the Review ends.
- A Stationary Object is never named or shown. When a Review holds more than one Tracked Object, only those Frigate reports as moving are named or shown. The first Notification waits briefly (3 seconds by default) so it can name all of them.

It reads Frigate's `frigate/reviews` and `frigate/events` MQTT topics.

### Set up

1. In the Frigate integration's options, keep the unauthenticated notification event proxy enabled. Set its expiry to `86400` seconds (24 hours).
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create an automation from it. Pick the Frigate camera and the Recipients' phones. Give the external URL phones use to reach Home Assistant, for example `https://ha.example.com`.

## Tests

`tests/` runs the blueprint in a real Home Assistant core, pinned in `pyproject.toml` to the release running live. The tests publish Frigate MQTT payloads and check what a Recipient receives.

```sh
uv run pytest
```
