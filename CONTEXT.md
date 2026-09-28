# Front Door Notifications

The system that tells household members, on their phones, about activity at the front door that is worth their attention, and lets them act on it right away.

## Language

### Activity

**Review**:
Frigate's grouping of activity on one camera over a continuous span of time. It can contain several Tracked Objects.
_Avoid_: Event, clip, incident

**Alert**:
A Review that warrants a Notification: a moving person in any Watched Zone, or a car arriving in the driveway. A car in street parking is never an Alert. This is the same thing as Frigate's *alert* severity; Frigate's *detection* severity is never an Alert.
_Avoid_: Detection, motion, event

**Watched Zone**:
One of the named areas of the camera view that matter: driveway, entry breezeway, street parking.
_Avoid_: Region, area

**Tracked Object**:
A single person or car that Frigate follows across frames inside a Review.
_Avoid_: Detection, event, object

**Stationary Object**:
A Tracked Object that is not moving, such as a parked car. It never causes a Notification and is never named in one.
_Avoid_: Parked object, static object

**Doorbell Press**:
A visitor pressing the front door doorbell button. It produces a Notification on its own, independent of any Review.
_Avoid_: Ring, visitor event

### Delivery

**Notification**:
The single push a Recipient gets for one Alert or Doorbell Press. It is updated in place as the Alert changes and when it ends. Only the first delivery makes a sound.
_Avoid_: Alert (that means something else here), message, push

**Quiet Window**:
The period after a Notification during which a Notification for a new Alert arrives without sound.
_Avoid_: Cooldown, debounce

**Recipient**:
A household member's phone that receives Notifications.
_Avoid_: Device, target, subscriber

**Snooze**:
A time-limited pause of Alert Notifications for one Recipient. It ends when it expires or when the Recipient resumes. It never holds back a Doorbell Press.
_Avoid_: Mute, silence, do-not-disturb
