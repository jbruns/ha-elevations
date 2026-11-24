"""`insteon refs DEVICE`: every Reference to the Device's entities, read-only.

The Device's entities are its isy994 entities, found by unique ID (one per
Button, perhaps with an `_OL`-style suffix), plus any other entity on the same
HA device, such as a `switch_as_x` wrapper. Its Scenes' switches are not among
them: a Scene outlives any one Device in it.

Live Home Assistant:
- automations, scripts, scenes, groups (old-style and group helpers) and
  persons, from HA's own `search/related`;
- every Lovelace dashboard HA will return a config for, storage or YAML.
Helpers that hold an entity in their config entry's options, such as
template and min_max helpers, are not searched: the API returns those options
only by starting an options flow, which is not a read. Neither are template
entities in HA's own YAML. The report names each such helper for a check by
hand.

The repo: each gitignored `*.local.yaml` overlay that gives an entity as a
token's value (ADR 0009), then every tracked file that uses `{TOKEN}`, and
every tracked file, such as a blueprint input or a test, that names the entity
directly. Overlay values are never printed, and nothing is written.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from insteon.address import ButtonAddress
from insteon.ha import DOMAIN, HaClient, HaError, HaRegistry, controller_suffix
from insteon.iox import IoxClient
from insteon.network import Device, Network
from insteon.resolve import ha_entity_ids, resolve_device

# What `search/related` finds that names an entity, in the order printed.
_RELATED = ("automation", "script", "scene", "group", "person")
# Helper integrations whose config (the entities they read) HA does not return.
_OPAQUE_HELPERS = frozenset(
    {
        "derivative", "filter", "history_stats", "integration", "min_max", "mold_indicator",
        "statistics", "template", "threshold", "tod", "trend", "utility_meter",
    }
)


class RepoError(RuntimeError):
    """The repo cannot be searched."""


@dataclass(frozen=True)
class DeviceEntity:
    entity_id: str
    platform: str
    """`isy994`, or the helper's integration, such as `switch_as_x`."""


@dataclass(frozen=True)
class RepoReference:
    entity_id: str
    path: str
    line: int
    token: str | None
    """The overlay token the file uses, or None when it names the entity directly."""


@dataclass(frozen=True)
class OverlayToken:
    entity_id: str
    token: str
    overlay: str


@dataclass(frozen=True)
class RefsReport:
    device: Device
    entities: tuple[DeviceEntity, ...]
    related: dict[str, dict[str, list[str]]]
    """By entity ID: the kinds in `_RELATED` and what of each names it."""
    dashboards: dict[str, list[str]]
    """By entity ID: `<dashboard>: <path in its config>` for each mention."""
    unreadable_dashboards: tuple[str, ...]
    unsearched_helpers: tuple[str, ...]
    """`<integration>: <title>` for each helper whose config HA does not return."""
    tokens: tuple[OverlayToken, ...]
    in_repo: tuple[RepoReference, ...]
    repo: Path


async def refs(iox: IoxClient, ha: HaClient, name: str, *, repo: Path) -> RefsReport:
    network = await iox.network()
    registry = await ha.registry()
    device = resolve_device(name, network, registry)
    entities = device_entities(device, network, registry)
    entity_ids = [entity.entity_id for entity in entities]

    related = {}
    for entity_id in entity_ids:
        search = await ha.related(entity_id)
        related[entity_id] = {kind: sorted(search[kind]) for kind in _RELATED if search.get(kind)}

    mentions: dict[str, list[str]] = {entity_id: [] for entity_id in entity_ids}
    unreadable = []
    for title, url_path in [("Overview (default)", None)] + [
        (board.get("title") or board["url_path"], board["url_path"]) for board in await ha.dashboards()
    ]:
        label = title if url_path is None else f"{title} ({url_path})"
        try:
            config = await ha.dashboard_config(url_path)
        except HaError as error:
            if url_path is None and error.code == "config_not_found":
                continue  # the default dashboard is auto-generated: it holds no config to search
            unreadable.append(f"{label}: {error}")
            continue
        for entity_id, path in find_mentions(config, entity_ids):
            mentions[entity_id].append(f"{label}: {path}")

    tokens, found = repo_references(repo, entity_ids)
    return RefsReport(
        device=device,
        entities=tuple(entities),
        related=related,
        dashboards=mentions,
        unreadable_dashboards=tuple(unreadable),
        unsearched_helpers=tuple(
            sorted(
                f"{entry['domain']}: {entry.get('title', '')}"
                for entry in await ha.config_entries()
                if entry.get("domain") in _OPAQUE_HELPERS
            )
        ),
        tokens=tuple(tokens),
        in_repo=tuple(found),
        repo=repo,
    )


def device_entities(device: Device, network: Network, registry: HaRegistry) -> list[DeviceEntity]:
    """The isy994 entities of the Device's Buttons, and other entities on its HA device."""
    isy994 = [DeviceEntity(entity_id, DOMAIN) for entity_id in ha_entity_ids(device, network, registry)]

    def names_a_button(identifier: str) -> bool:
        suffix = controller_suffix(identifier, network.uuid)
        button = ButtonAddress.try_parse(suffix) if suffix else None
        return button is not None and button.device == device.address

    ha_devices = {
        ha_device["id"]
        for ha_device in registry.isy994_devices()
        if any(names_a_button(identifier) for identifier in registry.isy994_identifiers(ha_device))
    }
    helpers = sorted(
        (
            DeviceEntity(entity["entity_id"], entity.get("platform", ""))
            for entity in registry.entities
            if entity.get("platform") != DOMAIN and entity.get("device_id") in ha_devices
        ),
        key=lambda entity: entity.entity_id,
    )
    return isy994 + helpers


def _entity_pattern(entity_ids: Iterable[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(entity_id) for entity_id in sorted(entity_ids, key=len, reverse=True))
    return re.compile(rf"(?<![\w.])({alternatives})(?![\w])")


def _entities_in(pattern: re.Pattern[str], text: str) -> list[str]:
    """The entity IDs `pattern` finds in `text`, each once, in order."""
    return list(dict.fromkeys(match[1] for match in pattern.finditer(text)))


def find_mentions(config: Any, entity_ids: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Each (entity ID, path) where a string in a dashboard config names the entity."""
    ids = list(entity_ids)
    if not ids:
        return
    pattern = _entity_pattern(ids)

    def walk(value: Any, path: str) -> Iterator[tuple[str, str]]:
        if isinstance(value, dict):
            for key, item in value.items():
                yield from walk(key, path)
                yield from walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            for entity_id in _entities_in(pattern, value):
                yield entity_id, path or "(top)"

    yield from walk(config, "")


def repo_references(repo: Path, entity_ids: Iterable[str]) -> tuple[list[OverlayToken], list[RepoReference]]:
    """Overlay tokens whose value is one of the entities, and tracked lines using them."""
    ids = list(entity_ids)
    if not ids:
        return [], []
    pattern = _entity_pattern(ids)
    tokens = []
    for overlay in _git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", "*.local.yaml"):
        if any(part.startswith(".") for part in Path(overlay).parts):
            continue
        for token, values in _overlay_values(repo / overlay).items():
            for entity_id in ids:
                object_id = entity_id.split(".", 1)[1]
                if any(value == object_id or entity_id in _entities_in(pattern, value) for value in values):
                    tokens.append(OverlayToken(entity_id, token, overlay))

    by_token: dict[str, list[str]] = {}
    for token in tokens:
        by_token.setdefault("{" + token.token + "}", []).append(token.entity_id)
    found = []
    for path in _git(repo, "ls-files", "-z"):
        try:
            text = (repo / path).read_text()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for placeholder, token_entities in by_token.items():
                if placeholder in line:
                    found.extend(
                        RepoReference(entity_id, path, number, placeholder[1:-1]) for entity_id in token_entities
                    )
            for entity_id in _entities_in(pattern, line):
                found.append(RepoReference(entity_id, path, number, None))
    return tokens, found


_TOKEN_KEY = re.compile(r"[A-Z][A-Z0-9_]+")


def _overlay_values(path: Path) -> dict[str, list[str]]:
    """Each token's value in an overlay, as strings (a YAML list value gives several).

    Read as the renderer reads it (`scripts/render_assets.py`, `load_overlay`):
    `TOKEN: value` lines taken literally, or YAML when the file is not in that
    form. Keys that are not token names are ignored.
    """
    text = path.read_text()
    data: dict[object, object] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(": ")
        if not sep:
            data = {}
            break
        data[key] = value
    if not data:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            loaded = None
        data = loaded if isinstance(loaded, dict) else {}
    values: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(key, str) and _TOKEN_KEY.fullmatch(key):
            items = value if isinstance(value, list) else [value]
            values[key] = [str(item) for item in items if item is not None]
    return values


def _git(repo: Path, *args: str) -> list[str]:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RepoError(f"cannot list files in {repo} with git: {error}") from error
    return [path for path in result.stdout.decode().split("\0") if path]


# Rendering ----------------------------------------------------------------


def render(report: RefsReport) -> str:
    device = report.device
    sections = [f"References to {device.address} {device.name}"]
    if not report.entities:
        sections.append("Home Assistant has no entities for this Device.")
        return "\n\n".join(sections) + "\n"

    lines = [f"Entities ({len(report.entities)})"]
    for entity in report.entities:
        helper = "" if entity.platform == DOMAIN else f"  ({entity.platform} helper on the same HA device)"
        lines.append(f"  {entity.entity_id}{helper}")
    sections.append("\n".join(lines))

    lines = ["Home Assistant"]
    for entity in report.entities:
        found = report.related.get(entity.entity_id, {})
        boards = report.dashboards.get(entity.entity_id, [])
        if not found and not boards:
            continue
        lines.append(f"  {entity.entity_id}")
        for kind, items in found.items():
            lines.extend(f"    {kind}: {item}" for item in items)
        lines.extend(f"    dashboard {board}" for board in boards)
    if len(lines) == 1:
        lines.append("  No References.")
    lines.extend(f"  Dashboard not read: {board}" for board in report.unreadable_dashboards)
    lines.append(
        "  Not searched, since the HA API does not return their config; check them by hand: "
        "template entities in HA's YAML outside the repo, and these helpers"
        + ("" if report.unsearched_helpers else ": none")
    )
    lines.extend(f"    {helper}" for helper in report.unsearched_helpers)
    sections.append("\n".join(lines))

    lines = [f"Repo ({report.repo})"]
    for token in report.tokens:
        lines.append(f"  {token.entity_id} is {{{token.token}}} in {token.overlay}")
        lines.extend(
            f"    {ref.path}:{ref.line}" for ref in report.in_repo
            if ref.token == token.token and ref.entity_id == token.entity_id
        )
    direct = [ref for ref in report.in_repo if ref.token is None]
    for entity_id in dict.fromkeys(ref.entity_id for ref in direct):
        lines.append(f"  {entity_id} named directly")
        lines.extend(f"    {ref.path}:{ref.line}" for ref in direct if ref.entity_id == entity_id)
    if len(lines) == 1:
        lines.append("  No References.")
    sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"
