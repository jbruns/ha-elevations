# Blueprints

Home Assistant imports these blueprints by URL, so they take every camera, phone and URL as an input (ADR 0004).

## Alert Notifications

`automation/jbruns/alert_notifications.yaml` sends one Notification per Alert on a Frigate camera to each Recipient.

- The title is the camera's name, for example "Front Door". The body names each moving Tracked Object and its Watched Zone, for example "Person in Entry Breezeway" or "Car arriving in Driveway". A recognized face or plate sub-label replaces the label.
- The image is the moving Tracked Object's cropped snapshot with a bounding box, never the Review preview GIF (ADR 0003).
- Only the first delivery makes a sound. Later updates replace it silently, each with a new image URL: when objects join, when Frigate's snapshot improves, and with the final best snapshot when the Review ends.
- The Quiet Window: a Notification for a new Alert within 2 minutes (the default) of the previous Notification's first delivery arrives without sound. The blueprint records each first delivery in a Date and time helper. Automations that share the helper share one Quiet Window.
- A Stationary Object is never named or shown. When a Review holds more than one Tracked Object, only those Frigate reports as moving are named or shown. The first Notification waits briefly (3 seconds by default) so it can name all of them.
- Tapping the Notification opens the front-door view in the Home Assistant app, showing the latest Review with its timeline (see `dashboards/README.md`). Its **Live** action opens the live cameras view. Both are paths within Home Assistant, so the app opens them itself, at home or away.

It reads Frigate's `frigate/reviews` and `frigate/events` MQTT topics.

### Set up

1. In the Frigate integration's options, keep the unauthenticated notification event proxy enabled. Set its expiry to `86400` seconds (24 hours).
2. Create a Date and time helper with date and time, for the Quiet Window: Settings → Devices & Services → Helpers → Create Helper → Date and/or time.
3. Add the front-door view to a dashboard: see `dashboards/README.md`.
4. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
5. Create an automation from it. Pick the Frigate camera, the Recipients' phones and the helper. Give the external URL phones use to reach Home Assistant, for example `https://ha.example.com`. Give the paths of the front-door view and the live cameras view, for example `/lovelace/front-door` and `/lovelace/cameras`.

## Doorbell Press Notifications

`automation/jbruns/doorbell_press_notifications.yaml` sends a Notification to each Recipient for every Doorbell Press.

- It is its own Notification, with its own tag and group. A press during an Alert adds a second Notification; the two are never merged, and later Alert updates never replace it.
- It is iOS *time-sensitive* and always makes a sound, so it breaks through Focus modes and the Quiet Window.
- Long-pressing it shows the camera's live stream.
- Tapping it, or its **Talk** action, opens the cameras view in the Home Assistant app, where two-way audio works.
- It never checks a Snooze, so a snoozed Recipient still gets it.

It triggers when the doorbell's button sensor turns on, for example a Reolink doorbell's Visitor binary sensor.

### Set up

1. Import the blueprint as for Alert Notifications, with this file's GitHub URL.
2. Create an automation from it. Pick the doorbell's button sensor, the camera to stream, and the Recipients' phones. Give the path of the cameras view with two-way audio, for example `/lovelace/cameras`.
3. In iOS Settings → Notifications → Home Assistant, turn on Time Sensitive Notifications, and allow the app in each Focus mode that should let it through.

## Tests

`tests/` runs the blueprints in a real Home Assistant core, pinned in `pyproject.toml` to the release running live. The tests publish Frigate MQTT payloads, press the doorbell, and check what a Recipient receives. They also check that the front-door view answers the Notification's URL action.

```sh
uv run pytest
```
