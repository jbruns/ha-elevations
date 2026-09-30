# Context Map

Home Assistant automations and Displays for one household. Blueprints that other households could use or adapt, plus this household's own config, templated so it stays public-safe (ADR 0009).

## Contexts

- [Front Door](./front-door/CONTEXT.md): tells household members about activity at the front door worth their attention
- [Climate](./climate/CONTEXT.md): keeps the home comfortable, and says when to open or close the windows
- [Infrastructure](./infrastructure/CONTEXT.md): tells the Administrator when the systems the home depends on fail or recover
- [Safety](./safety/CONTEXT.md): warns the household about hazards in the home, and readings past safe limits
- [Family](./family/CONTEXT.md): supports the children's routines, such as Screen Time and the school day
- [Lighting](./lighting/CONTEXT.md): turns lights on and off with the sun, the hour, and each other
- [Dashboards](./dashboards/CONTEXT.md): what the household sees at a glance, on Our Home and the Wallboard

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
- **Dashboards → every context**: Displays show what the other contexts own, such as Hazards and Exterior Doors. No context depends on a Display.
