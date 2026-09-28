# Infrastructure

Tells the Administrator when a system the home depends on fails or recovers, and which batteries need replacing.

## Language

**Monitored System**:
One piece of shared infrastructure that the home depends on, such as a radio network's controller, a UPS, the camera pipeline, the host's disk or the rack's temperature.
_Avoid_: Service, integration, device

**Failure**:
A Monitored System stays in a bad condition for longer than its grace period: unavailable, not in a healthy state, or past a limit.
_Avoid_: Alert, outage, incident

**Recovery**:
The end of a Failure, when the Monitored System is healthy again. Its Notification replaces the Failure's.
_Avoid_: Resolved, cleared

**Battery Digest**:
A daily list of batteries that are low or have stopped reporting, sent only when the list is not empty.
_Avoid_: Battery alert, low battery notification
