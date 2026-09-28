# Our own blueprint replaces the SgtBatten community blueprint

We are replacing the SgtBatten "Frigate Notifications" blueprint with a blueprint of our own, versioned in this repo. The community blueprint is highly general: dozens of inputs and many branches, and it targets Android as well as iOS. That generality made the empty-snapshot failure hard to see. Our needs are narrow: iOS actions, a per-Recipient Snooze, Doorbell Presses, and in-app review. Tweaking its inputs could not give us those needs, and forking it would mean carrying upstream changes we don't want.
