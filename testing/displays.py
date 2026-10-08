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

from scripts.render_assets import load_overlay, render_display, render_text

ENTITY = re.compile(r"\b[a-z_]+\.[a-z0-9_]+\b")
# A template that builds a card may name an action, such as 'perform_action': 'button.press'.
ACTION = re.compile(r"""['"]perform_action['"]\s*:\s*['"][a-z_]+\.[a-z0-9_]+['"]""")


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
    types = set()
    for node in walk(rendered):
        if (
            isinstance(node, dict)
            and isinstance(node.get("type"), str)
            and node["type"].startswith("custom:")
        ):
            card_type = node["type"].removeprefix("custom:")
            types.add("mushroom" if card_type.startswith("mushroom-") else card_type)
    return types


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
            found.update(ENTITY.findall(ACTION.sub("", node)))
    return found


def assert_entities_documented(rendered: dict[str, Any], source: DisplaySource) -> None:
    undocumented = sorted(referenced_entities(rendered) - source.prerequisites)
    assert not undocumented, f"entities missing from {source.path} prerequisites: {', '.join(undocumented)}"


async def async_load_support_packages(
    hass: HomeAssistant, source: DisplaySource, overlay: Path | None = None
) -> None:
    values = load_overlay(overlay) if overlay else {}
    for package in source.support_packages:
        rendered = render_text(package.read_text(), values, source=package)
        config = yaml.safe_load(rendered) or {}
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
    placeholder_states = {
        "binary_sensor": "off",
        "cover": "closed",
        "input_select": "false",
        "lock": "locked",
        "person": "home",
        "sensor": "0",
        "todo": "0",
        "weather": "sunny",
    }
    for entity in source.prerequisites:
        if not entity.startswith("input_datetime."):
            domain = entity.split(".", 1)[0]
            hass.states.async_set(entity, placeholder_states.get(domain, "idle"))


async def async_render_templates(hass: HomeAssistant, rendered: dict[str, Any]) -> list[str]:
    results = []
    def render_node(node: Any, variables: dict[str, Any] | None = None) -> None:
        if isinstance(node, dict):
            local_variables = variables
            if isinstance(node.get("entity"), str):
                local_variables = {**(variables or {}), "entity": node["entity"]}
            for value in node.values():
                render_node(value, local_variables)
        elif isinstance(node, list):
            for value in node:
                render_node(value, variables)
        elif isinstance(node, str) and ("{{" in node or "{%" in node):
            results.append(Template(node, hass).async_render(variables, parse_result=False))

    render_node(rendered)
    return results
