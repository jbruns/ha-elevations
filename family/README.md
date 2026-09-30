# Family

Supports the children's routines: how much they may watch, and what their school day holds. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the School Day and Special Classes blueprints.
- `tests/`: runs the blueprints in a real Home Assistant core.

Home Assistant imports these blueprints by URL, so they take every entity and every policy value as an input (ADR 0004).

## School Day

`blueprints/automation/family_school_day.yaml` publishes whether today and tomorrow are a **School Day**. It checks at 06:00 and when Home Assistant starts.

A School Day is:

- within the school year, from the event whose summary contains `first day of school` to the event whose summary contains `last day of school` on the district calendar;
- a weekday; and
- not a closure whose summary contains `no school` on the closures calendar.

The three phrases are blueprint inputs, so another district can use different wording.

### Set up

1. Create two Toggle helpers, in Settings → Devices & Services → Helpers → Create Helper:
   - **School Day Today**: read by Screen Time to decide which Viewing Window applies today.
   - **School Day Tomorrow**: read by Screen Time to decide whether this evening is before a School Day.
2. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
3. Create an automation from it. Pick the district calendar, the closures calendar and the two helpers. Change the summary phrases only if the calendars use different words.
4. Screen Time (#32) should read these Toggle helpers directly. It needs both: when today is a School Day and tomorrow is not, the evening Viewing Window is allowed; when both are School Days, no Viewing Window opens.

### Check it works

- On a normal weekday between the first and last day of school, today's helper is on.
- On weekends, closures, days before the first day and days after the last day, the matching helper is off.
- On Friday, tomorrow's helper is off unless Saturday is intentionally represented as a School Day by changing the blueprint rules in a future version.

### Cutover

1. Back up Home Assistant.
2. Create the new **School Day Tomorrow** Toggle helper. The existing live helper is a Dropdown with `true`/`false`; the Wallboard dashboard reads it, so keep it until the Wallboard is updated or point the dashboard at the new Toggle helper.
3. Create the **School Day Today** Toggle helper, or replace the old Dropdown only after checking every reader. If you keep both for a transition, leave the old automation enabled until the Wallboard no longer reads the Dropdown.
4. Import this blueprint and create one automation instance with the district calendar, closures calendar and both Toggle helpers.
5. Disable the old `[UI] isSchoolDay` automation, confirm both Toggle helpers update at the next run, then delete the old automation and the old Dropdown when nothing reads it.

## Special Classes

`blueprints/automation/family_special_classes.yaml` sets one child's **Special Class** helper each morning. Create one automation from it for each child.

- **Special Class helper**: a Dropdown helper for one child. Its options must include every class used in that child's rotation, plus `No class`. `Not set` is useful as the initial value.
- **Weekday rotation**: the Monday through Friday class names for that child. The blueprint uses text inputs so the classes can be typed directly in the UI.
- **Non-School Days**: weekends show `No class`. If the optional School Day helper is set, any day when that helper is off also shows `No class`. If the helper is left unset, every Monday through Friday uses the rotation.
- **Update time**: 06:05 by default, matching the live automation it replaces. Home Assistant start also updates the helper for the current day.
- **School Day changes**: when the optional School Day helper changes, the blueprint immediately re-selects the class or `No class`.

### Set up

1. Create one Dropdown helper per child. Use a placeholder-safe name in examples, such as `Example Special Class`; do not commit children's names or real helper entity IDs.
2. Add the helper options: `Not set`, `No class`, and every class in that child's rotation.
3. Import the blueprint: Settings → Automations & Scenes → Blueprints → Import Blueprint, with this file's GitHub URL.
4. Create one automation from it for each child. Set that child's helper and weekday rotation. Optionally set the School Day helper published by the School Day blueprint.

For example:

| Helper | Monday | Tuesday | Wednesday | Thursday | Friday | School Day helper |
| --- | --- | --- | --- | --- | --- | --- |
| `input_select.example_special_class` | Library | PE | Art | Music | Lab | `input_boolean.example_school_day` |

### Check it works

- Temporarily set the update time to a minute from now and confirm the helper changes to today's class.
- Turn the optional School Day helper off, run the automation, and confirm the helper shows `No class`. Turn it back on afterwards.
- The automation's traces show the selected class for each run.

### Cutover

1. Back up Home Assistant.
2. Create or verify each child's Special Class helper and options.
3. Import the blueprint and create one automation instance per child.
4. Let the new instances run, or run them by hand, and confirm each helper shows the expected class or `No class`.
5. Disable the old `[UI] Special Classes` automation.
6. After a successful school morning, delete the old automation. Keep the backup until the next school week has passed.
