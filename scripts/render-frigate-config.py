#!/usr/bin/env python3
"""Render frigate/config.yaml into a deployable Frigate config.

Every {FRIGATE_*} placeholder in the template is replaced with its value from
frigate/secrets.local.yaml (one `KEY: value` per line, taken literally). Fails
if the template uses a placeholder the secrets file does not define.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{(FRIGATE_[A-Z0-9_]+)\}")


def load_secrets(path: Path) -> dict[str, str]:
    secrets = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(": ")
        if not sep or not re.fullmatch(r"FRIGATE_[A-Z0-9_]+", key):
            sys.exit(f"{path}:{number}: expected 'FRIGATE_NAME: value'")
        secrets[key] = value
    return secrets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=ROOT / "frigate/config.yaml")
    parser.add_argument("--secrets", type=Path, default=ROOT / "frigate/secrets.local.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "frigate/build/config.yaml")
    args = parser.parse_args()

    template = args.template.read_text()
    secrets = load_secrets(args.secrets)

    missing = sorted(set(PLACEHOLDER.findall(template)) - secrets.keys())
    if missing:
        sys.exit(f"undefined in {args.secrets}: {', '.join(missing)}")

    rendered = PLACEHOLDER.sub(lambda m: secrets[m[1]], template)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    args.output.chmod(0o600)
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
