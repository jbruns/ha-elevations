# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in this repo's GitHub Issues, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Multi-context: `CONTEXT-MAP.md` at the repo root points to one `CONTEXT.md` per context in each context's folder (e.g. `climate/CONTEXT.md`). ADRs are system-wide, in `docs/adr/`. See `docs/agents/domain.md`.

## No secrets or PII

This repo is public; see `docs/adr/0004-public-repo-no-secrets-or-pii.md`. Never commit:

- Secrets: tokens, passwords, API keys, webhook IDs, notification proxy URLs.
- PII: people's names; hostnames and domains; entity or device IDs that embed a name or device (e.g. `notify.mobile_app_<name>`); recognized face or plate labels; addresses or coordinates; camera snapshots or recordings.

Blueprints take all such values as input selectors. Use placeholders like `camera.example` and `https://ha.example.com` in docs and examples.

Enforced by `pre-commit` (`pre-commit install` after cloning): gitleaks, plus `scripts/check-pii-denylist.sh`. That script checks staged files against `.pii-denylist`, a local, gitignored list of real names, hostnames and device IDs, one per line.

The Frigate config is a template with `{FRIGATE_*}` placeholders; see `front-door/frigate/README.md`. Never commit `front-door/frigate/*.local.yaml` or `front-door/frigate/build/`.

Private discovery notes about the live instance belong in the gitignored `.private/` folder. You may read them for context and add notes there, but never commit that folder or copy its real names, hostnames, URLs or entity IDs into tracked files.
