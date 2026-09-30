"""Shared Display test seam for rendered Home Assistant dashboards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.setup import async_setup_component

from scripts.render_assets import render_display

ENTITY = re.compile(r"\b[a-z_]+\.[a-z0-9_]+\b")


@dataclass(frozen=True)
class DisplaySource:
    path: Path
    config: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "DisplaySource":
        config = yaml.safe_load(path.read_text()) or {}
        if not isinstance(config, dict):
            raise ValueError(f"{path}: expected a display source mapping")
        return cls(path, config)

    @property
    def prerequisites(self) -> set[str]:
        raw = self.config.get("prerequisites", {}).get("entities", [])
        return set(raw)

    @property
    def support_packages(self) -> list[Path]:
        packages = self.config.get("support_packages", []) or []
        return [(self.path.parent / package).resolve() for package in packages]

    def render(self, overlay: Path | None = None) -> dict[str, Any]:
        return render_display(self.path, overlay)


def custom_card_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    cards = data.get("custom_cards", {})
    if not isinstance(cards, dict):
        raise ValueError(f"{path}: expected custom_cards mapping")
    return cards


def walk(node: Any) -> Iterable[Any]:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def custom_card_types(rendered: dict[str, Any]) -> set[str]:
    return {
        node["type"].removeprefix("custom:")
        for node in walk(rendered)
        if isinstance(node, dict)
        and isinstance(node.get("type"), str)
        and node["type"].startswith("custom:")
    }


def assert_custom_cards_listed(rendered: dict[str, Any], manifest_path: Path) -> None:
    listed = set(custom_card_manifest(manifest_path))
    used = custom_card_types(rendered)
    missing = sorted(used - listed)
    assert not missing, f"custom cards missing from {manifest_path}: {', '.join(missing)}"


def referenced_entities(rendered: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for node in walk(rendered):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"entity", "camera_entity"} and isinstance(value, str):
                    found.add(value)
                elif key == "entity_id":
                    if isinstance(value, str):
                        found.add(value)
                    elif isinstance(value, list):
                        found.update(v for v in value if isinstance(v, str))
                elif key == "entities" and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            found.add(item)
                        elif isinstance(item, dict) and isinstance(item.get("entity"), str):
                            found.add(item["entity"])
        elif isinstance(node, str) and ("{{" in node or "{%" in node):
            found.update(ENTITY.findall(node))
    return found


def assert_entities_documented(rendered: dict[str, Any], source: DisplaySource) -> None:
    undocumented = sorted(referenced_entities(rendered) - source.prerequisites)
    assert not undocumented, f"entities missing from {source.path} prerequisites: {', '.join(undocumented)}"


async def async_load_support_packages(hass: HomeAssistant, source: DisplaySource) -> None:
    for package in source.support_packages:
        config = yaml.safe_load(package.read_text()) or {}
        for domain, domain_config in config.items():
            assert await async_setup_component(hass, domain, {domain: domain_config})


async def async_create_placeholder_entities(hass: HomeAssistant, source: DisplaySource) -> None:
    input_datetime = {
        entity.split(".", 1)[1]: {"has_date": True, "has_time": True}
        for entity in source.prerequisites
        if entity.startswith("input_datetime.")
    }
    if input_datetime:
        assert await async_setup_component(hass, "input_datetime", {"input_datetime": input_datetime})
    for entity in source.prerequisites:
        if not entity.startswith("input_datetime."):
            hass.states.async_set(entity, "idle")


async def async_render_templates(hass: HomeAssistant, rendered: dict[str, Any]) -> list[str]:
    results = []
    for node in walk(rendered):
        if isinstance(node, str) and ("{{" in node or "{%" in node):
            results.append(Template(node, hass).async_render(parse_result=False))
    return results
