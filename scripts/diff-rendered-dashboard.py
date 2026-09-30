#!/usr/bin/env python3
"""Diff a rendered Display against a live storage dashboard, read-only.

This local-only tool never writes to Home Assistant. It renders the committed
Display source with a gitignored overlay, reads the live dashboard from either
an SSH cat of `.storage/lovelace.<url_path>` or a saved `ha_config_get_dashboard`
JSON response, and prints a unified diff.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from render_assets import render_display


def _storage_key(url_path: str) -> str:
    return "lovelace" if url_path in {"default", "lovelace"} else f"lovelace.{url_path}"


def _live_from_storage(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    config = data.get("data", {}).get("config")
    if not isinstance(config, dict):
        raise ValueError("storage JSON did not contain data.config")
    return config


def _live_from_mcp_response(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    config = data.get("config") or data.get("data", {}).get("config")
    if not isinstance(config, dict):
        raise ValueError("MCP JSON did not contain config")
    return config


def _ssh_read(host: str, url_path: str, config_dir: str) -> str:
    storage = f"{config_dir.rstrip('/')}/.storage/{_storage_key(url_path)}"
    result = subprocess.run(
        ["ssh", host, "cat", storage],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Display source YAML")
    parser.add_argument("--overlay", type=Path, required=True, help="gitignored local overlay")
    parser.add_argument("--url-path", required=True, help="dashboard url_path, e.g. lovelace")
    parser.add_argument("--ssh-host", help="SSH host for read-only .storage cat")
    parser.add_argument("--ha-config-dir", default="/config", help="Home Assistant config directory")
    parser.add_argument("--live-storage-json", type=Path, help="saved .storage/lovelace.<url_path> JSON")
    parser.add_argument("--mcp-dashboard-json", type=Path, help="saved ha_config_get_dashboard JSON response")
    args = parser.parse_args()

    sources = [bool(args.ssh_host), bool(args.live_storage_json), bool(args.mcp_dashboard_json)]
    if sum(sources) != 1:
        parser.error("choose exactly one of --ssh-host, --live-storage-json, or --mcp-dashboard-json")

    rendered = render_display(args.source, args.overlay)
    if args.ssh_host:
        live = _live_from_storage(_ssh_read(args.ssh_host, args.url_path, args.ha_config_dir))
    elif args.live_storage_json:
        live = _live_from_storage(args.live_storage_json.read_text())
    else:
        live = _live_from_mcp_response(args.mcp_dashboard_json.read_text())

    rendered_yaml = yaml.safe_dump(rendered, sort_keys=False).splitlines(keepends=True)
    live_yaml = yaml.safe_dump(live, sort_keys=False).splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            live_yaml,
            rendered_yaml,
            fromfile=f"live:{args.url_path}",
            tofile=str(args.source),
        )
    )
    if diff:
        sys.stdout.writelines(diff)
        return 1
    print("rendered Display matches live dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
