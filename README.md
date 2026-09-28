# ha-elevations

Home Assistant automations for one household, written as Blueprints that other households could use or adapt. Every camera, phone and URL is a blueprint input, so nothing here is specific to one home (ADR 0004).

## Contexts

The repo is organised by context (ADR 0007). Each context's folder holds its glossary (`CONTEXT.md`), README, blueprints, tests and any dashboards or other Home Assistant assets. [CONTEXT-MAP.md](./CONTEXT-MAP.md) describes the contexts and how they relate.

- [Front Door](./front-door/README.md): Notifications about activity at the front door.
- [Climate](./climate/CONTEXT.md): comfort, and when to open or close the windows.
- [Infrastructure](./infrastructure/CONTEXT.md): tells the Administrator when the home's systems fail or recover.
- [Safety](./safety/CONTEXT.md): warns the household about hazards in the home.

Architecture decisions are in [docs/adr/](./docs/adr/).

## Import a blueprint

1. Open the blueprint's file on GitHub, for example [front-door/blueprints/automation/front_door_alert_notifications.yaml](./front-door/blueprints/automation/front_door_alert_notifications.yaml), and copy its URL.
2. In Home Assistant: Settings → Automations & Scenes → Blueprints → Import Blueprint. Paste the URL.
3. Create an automation from the blueprint. The context's README lists what to set up first.

Home Assistant saves an imported blueprint as `blueprints/automation/<owner>/<filename>`, dropping the folders in this repo. So every blueprint filename starts with its context, and a test fails if two blueprints anywhere in the repo share a filename.

## Tests

The tests run the blueprints in a real Home Assistant core, pinned in `pyproject.toml`. Run every context's tests, plus the repo-wide checks in `tests/`:

```sh
uv run pytest
```

Before committing, install the hooks that keep secrets and PII out of the repo: `pre-commit install`. See [AGENTS.md](./AGENTS.md).
