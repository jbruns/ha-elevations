#!/usr/bin/env python3
"""Render front-door/frigate/config.yaml into a deployable Frigate config."""

from render_assets import _default_frigate

if __name__ == "__main__":
    raise SystemExit(_default_frigate())
