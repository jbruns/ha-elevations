# The repo is organised by context, and blueprint filenames carry their context

Each context (Front Door, Climate, Infrastructure, Safety) has its own top-level folder. It holds the context's glossary, README, blueprints, tests and any dashboards or other Home Assistant assets. So one theme lives in one place, rather than spread across `blueprints/`, `dashboards/` and `tests/`. ADRs remain a single numbered sequence in `docs/adr/`.

When Home Assistant imports a blueprint from a GitHub URL, it saves it as `blueprints/automation/<owner>/<filename>` and drops the repo path. Every blueprint from this repo therefore lands in one folder, so filenames must be unique across the whole repo. Each filename starts with its context, for example `climate_comfort_policy.yaml`, and a test fails on duplicates. Don't shorten the names or rely on folders to tell blueprints apart.

## Consequences

- Moving a blueprint between folders without renaming it doesn't affect automations already using it. Only its `source_url` needs updating. Renaming it changes its local path, so the automations using it must be re-pointed.
- The two Front Door blueprints were renamed once, with a prefix, when this layout was adopted.
