# CI: HA test gate, weekly canary and Renovate preset

This repo publishes two things other Home Assistant repos can use: a reusable workflow that runs their tests, and a Renovate preset. Both assume a uv project that tests with `pytest-homeassistant-custom-component` (phcc), pinned exactly in `pyproject.toml` with a committed `uv.lock`. Why the pin leads live HA is in [ADR 0008](./adr/0008-ha-test-pin-leads-live-ha.md).

## Reusable workflow: `.github/workflows/ha-tests.yml`

Python comes from `requires-python` via uv, so the jobs follow HA's Python.

| Input | Default | Meaning |
| --- | --- | --- |
| `mode` | `gate` | `gate`: `uv sync --frozen`, then `uv run pytest` on the pinned phcc. Fails if the pin is an HA pre-release. `canary`: the same tests on the newest phcc that pins a stable HA. Neither mode tests HA betas. |
| `pytest-args` | `""` | Extra pytest arguments, split on spaces. |

The canary keeps one rolling issue in the calling repo, labelled `ha-canary` (created if missing). It opens the issue on the first failure and comments on it at each failure after that. When the canary is green again, it comments and closes the issue. It never contacts Home Assistant.

Permissions, granted by the calling workflow:

- Gate: `contents: read`.
- Canary: `contents: read` and `issues: write`.

The gate, for example in `.github/workflows/ci.yml`:

```yaml
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  ha-tests:
    uses: jbruns/ha-elevations/.github/workflows/ha-tests.yml@main
```

The canary, for example in `.github/workflows/ha-canary.yml`:

```yaml
on:
  schedule:
    - cron: "0 15 * * 1"
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  canary:
    uses: jbruns/ha-elevations/.github/workflows/ha-tests.yml@main
    with:
      mode: canary
```

This repo calls the workflow the same way, with `uses: ./.github/workflows/ha-tests.yml`.

## Renovate preset: `renovate/ha.json`

Install the Renovate GitHub app on the repo, then extend the preset in `renovate.json`:

```json
{
  "extends": ["github>jbruns/ha-elevations//renovate/ha"]
}
```

What it sets:

- Runs before 6am on Mondays (America/Los_Angeles), with the Dependency Dashboard on. `lockFileMaintenance` runs monthly.
- Groups: `dev-tools` (dev dependency groups and pre-commit hooks), `github-actions`, and `ha-pin` (phcc) on its own.
- Automerges on green CI only patch and minor updates of dev tools and GitHub Actions. Renovate merges these itself after checks pass (`platformAutomerge: false`), so no branch protection is needed for failing PRs to stay open. `ha-pin`, runtime dependencies and every major update wait for review.
- Updates pre-commit hooks.
- Leaves `requires-python` alone. Raise it by hand when HA moves to a new Python (ADR 0008).

phcc bumps count as `patch` updates to Renovate, because phcc versions are `0.13.N`. The `ha-pin` rule comes last so that it overrides the dev-tools automerge.

## What we've learned

- phcc publishes no pre-release versions. Instead, some of its ordinary releases pin an HA beta, for example phcc 0.13.360 pinned `homeassistant==2026.9.0b3`. So the canary reads each phcc release's `homeassistant==` requirement on PyPI and skips any that pin a beta. Renovate can still propose an `ha-pin` PR onto a beta. The gate fails such a PR until phcc pins the stable release.
