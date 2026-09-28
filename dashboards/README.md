# Dashboards

Views to add to a Home Assistant dashboard. They use placeholders like `camera.example` (ADR 0004); replace them when you add a view.

## Front-door view

`front_door_view.yaml` is the view that tapping an Alert Notification opens. It shows the latest Review for the camera in the [Advanced Camera Card](https://github.com/dermotduffy/advanced-camera-card), with a timeline below it. Drag the timeline to scrub the recording around the moment.

- It is a subview, so it has a back arrow and no tab of its own.
- It shows one camera. For a second camera, add another copy of the view with its own path, and give that path to that camera's automation.
- It opens the latest Review, whether or not it is marked reviewed in Frigate. If a newer Review exists than the one that sent the Notification, the view opens that one; scrub back to find the Alert.
- The card's `card_id` is `alert_review`. The Notification opens the view with the URL action `?advanced-camera-card-action.alert_review.review`, so the card loads the latest Review even when the app already has the view open. Keep the `card_id` if you edit the view.

### Add it

1. Open the dashboard, then Edit dashboard → ⋮ → Raw configuration editor.
2. Paste the file's contents as a new item under `views:`. Replace `camera.example` with the Frigate camera.
3. Save. The view's path is `/<dashboard>/front-door`, for example `/lovelace/front-door`. Give that path as the Review view in the Alert Notifications blueprint.

The Live action of a Notification opens a view you already have with a live camera card, for example a cameras view with two-way audio. Give its path as the blueprint's Live view.
