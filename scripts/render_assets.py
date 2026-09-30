#!/usr/bin/env python3
"""Render tokenised household assets into gitignored, deployable output."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TOKEN = re.compile(r"(?<!\$)\{([A-Z][A-Z0-9_]+)\}")
FRIGATE = ROOT / "front-door" / "frigate"


class UndefinedTokenError(ValueError):
    """Raised when a template uses a token the overlay does not define."""


def _literal_overlay(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(": ")
        if not sep:
            raise ValueError(f"{path}:{number}: expected 'TOKEN: value'")
        values[key] = value
    return values


def load_overlay(path: Path, *, token_pattern: re.Pattern[str] | None = None) -> dict[str, str]:
    pattern = token_pattern or TOKEN
    raw = _literal_overlay(path)
    if not raw:
        raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping of TOKEN: value")
    values: dict[str, str] = {}
    key_pattern = re.compile(r"^[A-Z][A-Z0-9_]+$")
    for key, value in raw.items():
        if not isinstance(key, str) or not key_pattern.fullmatch(key):
            raise ValueError(f"{path}: expected token keys like CONTEXT_NAME")
        if not pattern.fullmatch("{" + key + "}"):
            raise ValueError(f"{path}: token {key} is outside this renderer's token set")
        values[key] = "" if value is None else str(value)
    return values


def render_text(template: str, values: dict[str, str], *, source: Path | None = None) -> str:
    missing = sorted(set(TOKEN.findall(template)) - values.keys())
    if missing:
        location = f" in {source}" if source else ""
        raise UndefinedTokenError(f"undefined token{location}: {', '.join(missing)}")
    return TOKEN.sub(lambda match: values[match[1]], template)


def render_template_file(template: Path, overlay: Path, output: Path) -> Path:
    rendered = render_text(template.read_text(), load_overlay(overlay), source=template)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)
    output.chmod(0o600)
    return output


def _source_paths(source: Path) -> list[Path]:
    data = yaml.safe_load(source.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected a display source mapping")
    views = data.get("views")
    if not isinstance(views, list) or not views:
        raise ValueError(f"{source}: expected a non-empty views list")
    paths = []
    for view in views:
        if not isinstance(view, str):
            raise ValueError(f"{source}: every view must be a path string")
        paths.append((source.parent / view).resolve())
    return paths


def _render_yaml_file(path: Path, values: dict[str, str]) -> Any:
    rendered = render_text(path.read_text(), values, source=path)
    return yaml.safe_load(rendered)


def _render_view(path: Path, values: dict[str, str]) -> dict[str, Any]:
    view = _render_yaml_file(path, values)
    if not isinstance(view, dict):
        raise ValueError(f"{path}: expected a view mapping")
    sections = view.get("sections")
    if isinstance(sections, list) and all(isinstance(section, str) for section in sections):
        view["sections"] = [_render_yaml_file((path.parent / section).resolve(), values) for section in sections]
    return view


def render_display(source: Path, overlay: Path | None = None) -> dict[str, Any]:
    values = load_overlay(overlay) if overlay else {}
    source_config = yaml.safe_load(source.read_text()) or {}
    if not isinstance(source_config, dict):
        raise ValueError(f"{source}: expected a display source mapping")
    display: dict[str, Any] = {}
    views = []
    for path in _source_paths(source):
        views.append(_render_view(path, values))
    display["views"] = views
    display.update(
        {
            key: value
            for key, value in source_config.items()
            if key not in {"views", "url_path", "prerequisites", "support_packages"}
        }
    )
    return display


def write_display(source: Path, overlay: Path | None, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(render_display(source, overlay), sort_keys=False))
    output.chmod(0o600)
    return output


def _default_frigate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Front Door Frigate config")
    parser.add_argument("--template", type=Path, default=FRIGATE / "config.yaml")
    parser.add_argument("--secrets", type=Path, default=FRIGATE / "secrets.local.yaml")
    parser.add_argument("--output", type=Path, default=FRIGATE / "build" / "config.yaml")
    args = parser.parse_args(argv)
    try:
        written = render_template_file(args.template, args.secrets, args.output)
    except (OSError, ValueError) as err:
        sys.exit(str(err))
    print(f"wrote {written.resolve().relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="render one tokenised file")
    template.add_argument("--template", type=Path, required=True)
    template.add_argument("--overlay", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)

    display = sub.add_parser("display", help="assemble a dashboard from view files")
    display.add_argument("--source", type=Path, required=True)
    display.add_argument("--overlay", type=Path)
    display.add_argument("--output", type=Path, required=True)

    frigate = sub.add_parser("frigate", help="render the Front Door Frigate config")
    frigate.add_argument("--template", type=Path, default=FRIGATE / "config.yaml")
    frigate.add_argument("--overlay", type=Path, default=FRIGATE / "secrets.local.yaml")
    frigate.add_argument("--output", type=Path, default=FRIGATE / "build" / "config.yaml")

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            written = render_template_file(args.template, args.overlay, args.output)
        elif args.command == "display":
            written = write_display(args.source, args.overlay, args.output)
        else:
            written = render_template_file(args.template, args.overlay, args.output)
    except (OSError, ValueError) as err:
        sys.exit(str(err))
    print(f"wrote {written.resolve().relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
