# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in this repo's GitHub Issues, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## No secrets or PII

This repo is public; see `docs/adr/0004-public-repo-no-secrets-or-pii.md`. Never commit:

- Secrets: tokens, passwords, API keys, webhook IDs, notification proxy URLs.
- PII: people's names; hostnames and domains; entity or device IDs that embed a name or device (e.g. `notify.mobile_app_<name>`); recognized face or plate labels; addresses or coordinates; camera snapshots or recordings.

Blueprints take all such values as input selectors. Use placeholders like `camera.example` and `https://ha.example.com` in docs and examples.

Enforced by `pre-commit` (`pre-commit install` after cloning): gitleaks, plus `scripts/check-pii-denylist.sh`. That script checks staged files against `.pii-denylist`, a local, gitignored list of real names, hostnames and device IDs, one per line.

The Frigate config is a template with `{FRIGATE_*}` placeholders; see `frigate/README.md`. Never commit `frigate/*.local.yaml` or `frigate/build/`.
