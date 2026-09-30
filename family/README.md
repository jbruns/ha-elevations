# Family

Supports the children's routines: how much they may watch, and what their school day holds. The glossary is in [CONTEXT.md](./CONTEXT.md).

This folder holds:

- `blueprints/automation/`: the Special Classes blueprint.
- `tests/`: runs the blueprints in a real Home Assistant core.

Every entity and every schedule value is an input (ADR 0004).

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
