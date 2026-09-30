# The HA test pin leads live Home Assistant

The tests run against the Home Assistant core that `pytest-homeassistant-custom-component` (phcc) pins, and `pyproject.toml` pins phcc exactly. That pin used to mean "the HA release running live". It now means "the newest HA release the tests pass on", and live HA follows it. Renovate opens an `ha-pin` PR when phcc releases for a new HA, and the CI gate runs every test against it. A green `ha-pin` PR therefore says live HA is safe to upgrade to that release. Merge it just before or just after upgrading live HA. It is never automerged.

Some phcc releases pin an HA beta (phcc 0.13.360 pinned `homeassistant==2026.9.0b3`). phcc has no pre-release versions of its own, so Renovate proposes these like any other release. The gate fails when the pin is an HA pre-release, because live HA can't upgrade to it. The `ha-pin` PR stays red until phcc pins the stable release, and Renovate then moves the same PR onto it. The weekly canary is where betas are tested.

## Consequences

- Between merging an `ha-pin` PR and upgrading, the tests run a newer HA than the one live. Keep that window short.
- Don't "fix" the pin back to the live version. If live HA can't be upgraded yet, leave the `ha-pin` PR open instead.
- `requires-python` is not bumped by Renovate. When HA moves to a new Python, the `ha-pin` PR fails to lock, and `requires-python` is raised by hand in that PR.
