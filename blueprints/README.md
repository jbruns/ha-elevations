# Blueprints

Home Assistant imports these blueprints by URL, so they take every camera, phone and URL as an input (ADR 0004).

## Start from scratch

Follow this if you have no Frigate notifications yet. The per-blueprint **Set up** sections below have the details for each step.

### Before you start

You need all of these:

- **Home Assistant 2025.4 or later**, reachable from outside your home network at an HTTPS URL, for example `https://ha.example.com`. Phones fetch snapshots from this URL, so it must work away from home.
- **Frigate**, with each camera's Watched Zones set up, publishing to an MQTT broker. The blueprint reads the `frigate/reviews` and `frigate/events` topics.
- **The MQTT integration** in Home Assistant, connected to the same broker.
- **The Frigate integration** (from HACS), with the unauthenticated notification event proxy enabled. This is a checkbox when you add the integration. It is also under the integration's options.
- **The Home Assistant app on each Recipient's iPhone**, signed in, with notifications allowed. Each phone then has a `notify.mobile_app_<phone>` action. The Notifications use iOS features: attachments, time-sensitive delivery, and actions that carry data back to Home Assistant.
- **The [Advanced Camera Card](https://github.com/dermotduffy/advanced-camera-card)** (from HACS), for the front-door view.
- **A dashboard view with a live camera card that has two-way audio**, such as a cameras view, if you want the Live and Talk actions to open one.

### Let Frigate decide what an Alert is

The blueprint sends a Notification only for Reviews that Frigate marks *alert*. It never filters by label or zone itself (ADR 0001). So set the Alert rule in the Frigate config, per camera. `frigate/config.yaml` in this repo is a working example:

- Under `zones:`, give each Watched Zone an `objects:` list of the labels that count there. For example, `person` everywhere and `car` in the driveway only.
- Under `review: alerts: required_zones:`, list the Watched Zones, so activity outside them is never an Alert.

Restart Frigate. Walk into a Watched Zone and check that the Frigate UI shows an *alert* Review.

### Set up Home Assistant

1. In the Frigate integration's options, set the notification proxy's expiry to `86400` seconds (24 hours).
2. Create the helpers: one Date and time helper for the Quiet Window, and one Snooze helper per Recipient. See [Alert Notifications → Set up](#set-up), steps 2 and 3.
3. Add the front-door view to a dashboard (`dashboards/README.md`). Note its path, for example `/lovelace/front-door`, and the path of your live cameras view.
4. Import both blueprints: Settings → Automations & Scenes → Blueprints → Import Blueprint, with each file's GitHub URL.
5. Create one Alert Notifications automation per camera. The Review view and Live view inputs default to `/lovelace/front-door` and `/lovelace/cameras`. If your views are on another dashboard, give their paths instead.
6. Create one Doorbell Press Notifications automation, if you have a doorbell. Turn off any other automation that notifies on a doorbell press, or Recipients get two Notifications per press.
7. On each iPhone, turn on Time Sensitive Notifications for the Home Assistant app ([Doorbell Press Notifications → Set up](#set-up-1), step 3).

### Check it works

- Walk into a Watched Zone. Each Recipient gets one Notification with a sound, showing you in the image. Updates arrive silently.
- Tap the Notification. The front-door view opens in the app, on the latest Review.
- Tap **Snooze 30 min** on one phone. That Notification reads "Snoozed until HH:MM", and the front-door view shows the Snooze. Tap **Resume** there to end it.
- Press the doorbell. Each Recipient gets a separate, time-sensitive Notification.

The phone's device name sets both its `notify.mobile_app_<phone>` action and its Snooze helper's entity ID. If you rename a phone in the app, rename its Snooze helper to match.

## Alert Notifications

`automation/jbruns/alert_notifications.yaml` sends one Notification per Alert on a Frigate camera to each Recipient.

- The title is the camera's name, for example "Front Door". The body names each moving Tracked Object and its Watched Zone, for example "Person in Entry Breezeway" or "Car arriving in Driveway". A recognized face or plate sub-label replaces the label.
- The image is the moving Tracked Object's cropped snapshot with a bounding box, never the Review preview GIF (ADR 0003).
- Only the first delivery makes a sound. Later updates replace it silently, each with a new image URL: when objects join, when Frigate's snapshot improves, and with the final best snapshot when the Review ends.
- The Quiet Window: a Notification for a new Alert within 2 minutes (the default) of the previous Notification's first delivery arrives without sound. The blueprint records each first delivery in a Date and time helper. Automations that share the helper share one Quiet Window.
- A Stationary Object is never named or shown. When a Review holds more than one Tracked Object, only those Frigate reports as moving are named or shown. The first Notification waits briefly (3 seconds by default) so it can name all of them.
- Tapping the Notification opens the front-door view in the Home Assistant app, showing the latest Review with its timeline (see `dashboards/README.md`). Its **Live** action opens the live cameras view. Both are paths within Home Assistant, so the app opens them itself, at home or away.
- Its **Snooze 30 min** and **Snooze 2 h** actions start a Snooze for that phone only. The tapped Notification changes in place, silently, to read "Snoozed until HH:MM". During a Snooze the phone gets no Alert Notifications or updates to them; other Recipients are unaffected. When the Snooze expires nothing is sent; the next Alert or update arrives as usual. Resume ends a Snooze early from the front-door view.

It reads Frigate's `frigate/reviews` and `frigate/events` MQTT topics.

### Set up

1. In the Frigate integration's options, keep the unauthenticated notification event proxy enabled. Set its expiry to `86400` seconds (24 hours).
2. Create a Date and time helper with date and time, for the Quiet Window: Settings → Devices & Services → Helpers → Create Helper → Date and/or time.
3. For each Recipient, create a Snooze helper the same way, with date and time. Its entity ID must be `input_datetime.snooze_<phone>`, where `<phone>` is the phone's device name as in its `notify.mobile_app_<phone>` action. For example, name the helper "Snooze Phone A" for a phone named "Phone A". A phone without one is never offered Snooze.
4. Add the front-door view to a dashboard: see `dashboards/README.md`.
5. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
6. Create an automation from it. Pick the Frigate camera, the Recipients' phones and the Quiet Window helper. Give the external URL phones use to reach Home Assistant, for example `https://ha.example.com`. Give the paths of the front-door view and the live cameras view, for example `/lovelace/front-door` and `/lovelace/cameras`.

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
