#!/usr/bin/env bash
# Fails if any staged file contains a term from the local, gitignored .pii-denylist.
set -euo pipefail

denylist="$(git rev-parse --show-toplevel)/.pii-denylist"

if [[ ! -s "$denylist" ]]; then
  echo "warning: .pii-denylist missing or empty; skipping PII check (see AGENTS.md)" >&2
  exit 0
fi

patterns="$(grep -vE '^\s*(#|$)' "$denylist" || true)"
[[ -z "$patterns" ]] && exit 0

status=0
for file in "$@"; do
  if matches="$(grep -niF -- "$patterns" "$file")"; then
    echo "$file: contains a denylisted term:" >&2
    echo "$matches" | cut -c1-200 >&2
    status=1
  fi
done
exit $status
