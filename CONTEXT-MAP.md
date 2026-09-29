# Context Map

Home Assistant automations for one household, written as Blueprints that other households could use or adapt.

## Contexts

- [Front Door](./front-door/CONTEXT.md): tells household members about activity at the front door worth their attention
- [Climate](./climate/CONTEXT.md): keeps the home comfortable, and says when to open or close the windows
- [Infrastructure](./infrastructure/CONTEXT.md): tells the Administrator when the systems the home depends on fail or recover
- [Safety](./safety/CONTEXT.md): warns the household about hazards in the home, and readings past safe limits

## Shared language

**Recipient**:
A household member's phone that receives household Notifications.
_Avoid_: Device, target, subscriber

**Administrator**:
The phone of the person who maintains the home's systems. It alone receives Infrastructure Notifications, which are never sent to every Recipient.
_Avoid_: Admin group, maintenance, owner

## Relationships

- **Front Door, Climate, Safety → Recipients**: these contexts notify household members.
- **Infrastructure → Administrator**: only this context notifies the Administrator, and it never notifies Recipients.
