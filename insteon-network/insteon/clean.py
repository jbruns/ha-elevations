"""`remove --clean`: take a removed Device's References out of live Home Assistant.

Only through HA's own config API, never `.storage`: automations, scripts and
scenes HA's editor can save, and storage-mode dashboards. Each edit is shown
as a diff first. Applying it to live HA is an explicit choice on each run
(#79): it needs `--apply`, and the Administrator confirms the diffs before
anything is saved.

What an edit does, in one config:
- An entity is taken out of every list that names it, and a scene's entity
  out of its `entities`.
- A trigger, condition, action, card or row left naming nothing (its
  `entity_id`, `entity` or `device_id` emptied) is deleted, and so is a card
  or stack it leaves empty: a container is deleted only when it would be left
  empty.
- An automation, script, scene or dashboard itself is never deleted. Nor is a
  condition: dropping one would change when something runs or shows. Nor is
  anything named in a template or in a form the tool cannot edit. Each of
  those is left untouched and listed for a hand edit.

Groups, persons, helpers, YAML-defined items and YAML dashboards can't be
saved through the config API, so they are listed for a hand edit too.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from insteon.ha import HaClient, HaError
from insteon.refs import find_mentions

AUTOMATION, SCRIPT, SCENE, DASHBOARD = "automation", "script", "scene", "dashboard"
_EDITABLE = (AUTOMATION, SCRIPT, SCENE)
_BY_HAND = ("group", "person")
_DEAD = object()
"""What a value becomes when removing the References leaves it naming nothing."""


class _Refused(Exception):
    pass


@dataclass(frozen=True)
class Stripped:
    config: Any
    """The config without the References, or None when the edit is refused."""
    changed: bool = False
    refusal: str | None = None


def strip(config: Any, entities: Iterable[str], devices: Iterable[str], *, kind: str) -> Stripped:
    """`config` without any reference to `entities` (entity IDs) or `devices` (HA device IDs)."""
    entity_ids, device_ids = frozenset(entities), frozenset(devices)
    by_key = {"entity_id": entity_ids, "entity": entity_ids, "device_id": device_ids}

    def walk(value: Any, path: str, root: bool) -> Any:
        if isinstance(value, list):
            kept = []
            for index, item in enumerate(value):
                if isinstance(item, str) and item in entity_ids:
                    continue
                new = walk(item, f"{path}[{index}]", False)
                if new is not _DEAD:
                    kept.append(new)
            return _DEAD if value and not kept else kept
        if not isinstance(value, dict):
            return value
        kept_items: dict[Any, Any] = {}
        emptied: str | None = None
        dropped_key = False
        for key, item in value.items():
            where = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and key in entity_ids:
                dropped_key = True
                continue
            names = by_key.get(key) if isinstance(key, str) else None
            if names is not None and isinstance(item, str):
                if item in names:
                    emptied = emptied or where
                else:
                    kept_items[key] = item
                continue
            if names is not None and isinstance(item, list) and all(isinstance(x, str) for x in item):
                rest = [x for x in item if x not in names]
                if item and not rest:
                    emptied = emptied or where
                else:
                    kept_items[key] = rest
                continue
            new = walk(item, where, False)
            if new is _DEAD:
                emptied = emptied or where
                continue
            kept_items[key] = new
        if dropped_key and value and not kept_items:
            return _DEAD
        if emptied is None:
            return kept_items
        if root:
            raise _Refused(f"removing it would leave {emptied} empty, and the tool never deletes a whole {kind}")
        if "condition" in value:
            raise _Refused(f"removing it would drop the condition at {path}, which changes when it runs")
        staying = next((key for key, item in kept_items.items() if isinstance(item, list) and item), None)
        if staying is not None:
            where = f"{path}.{staying}" if path else str(staying)
            raise _Refused(f"removing it would leave {emptied} empty while {where} stays")
        return _DEAD

    try:
        after = walk(config, "", True)
    except _Refused as refused:
        return Stripped(None, refusal=f"{refused}; edit it by hand")
    if after is _DEAD:
        return Stripped(None, refusal=f"removing it would leave the {kind} empty, and the tool never deletes one; edit it by hand")
    leftover = next(iter(find_mentions(after, [*entity_ids, *device_ids])), None)
    if leftover is not None:
        name, path = leftover
        return Stripped(None, refusal=f"it names {name} at {path}, in a form the tool cannot edit; edit it by hand")
    return Stripped(after, changed=after != config)


# Planning and applying --------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One place in live HA that names the Device."""

    kind: str
    key: str
    """The entity ID of an automation, script or scene; a dashboard's url_path, or "" for the default."""
    label: str

    @property
    def url_path(self) -> str | None:
        return self.key or None


@dataclass(frozen=True)
class Edit:
    item: Item
    before: Any
    after: Any

    def diff(self) -> str:
        before = yaml.safe_dump(self.before, sort_keys=False, allow_unicode=True).splitlines()
        after = yaml.safe_dump(self.after, sort_keys=False, allow_unicode=True).splitlines()
        return "\n".join(difflib.unified_diff(before, after, self.item.label, self.item.label, lineterm=""))


@dataclass(frozen=True)
class HandEdit:
    label: str
    reason: str


@dataclass(frozen=True)
class CleanPlan:
    entities: tuple[str, ...]
    """The entities whose References go: the Device's isy994 entities."""
    devices: tuple[str, ...]
    """The Device's HA device IDs, as device triggers and targets name them."""
    edits: tuple[Edit, ...] = ()
    by_hand: tuple[HandEdit, ...] = ()

    @property
    def references(self) -> int:
        return len(self.edits) + len(self.by_hand)


@dataclass
class CleanResult:
    saved: list[Edit] = field(default_factory=list)
    by_hand: list[HandEdit] = field(default_factory=list)
    backup: Path | None = None


async def plan(ha: HaClient, entities: Sequence[str], devices: Sequence[str], helpers: Sequence[str] = ()) -> CleanPlan:
    """Every Reference to the entities in live HA, as an edit or a hand edit. Reads only."""
    items: dict[Item, None] = {}
    by_hand: dict[str, str] = {}
    searches = [("entity", entity_id) for entity_id in entities] + [("device", device_id) for device_id in devices]
    for item_type, item_id in searches:
        found = await ha.related(item_id, item_type=item_type)
        for kind in _EDITABLE:
            for entity_id in found.get(kind, []):
                items[Item(kind, entity_id, f"{kind} {entity_id}")] = None
        for kind in _BY_HAND:
            for name in found.get(kind, []):
                by_hand[f"{kind} {name}"] = f"a {kind}, which HA's config API cannot edit; edit it by hand"
    for helper in helpers:
        by_hand[f"helper {helper}"] = "a helper on the Device's HA device; delete or repoint it by hand"

    edits = []
    for item in items:
        outcome = await _edit(ha, item, entities, devices)
        if isinstance(outcome, Edit):
            edits.append(outcome)
        elif outcome is not None:
            by_hand[item.label] = outcome

    boards = [("", "Overview (default)", "storage")] + [
        (board["url_path"], board.get("title") or board["url_path"], board.get("mode", "storage"))
        for board in await ha.dashboards()
    ]
    for url_path, title, mode in boards:
        item = Item(DASHBOARD, url_path, f"dashboard {title}" + (f" ({url_path})" if url_path else ""))
        try:
            config = await ha.dashboard_config(item.url_path)
        except HaError as error:
            if error.code != "config_not_found":
                by_hand[item.label] = f"not read ({error}); check it by hand"
            continue
        if not any(True for _ in find_mentions(config, [*entities, *devices])):
            continue
        if mode != "storage":
            by_hand[item.label] = "a YAML dashboard, which HA's config API cannot save; edit it by hand"
            continue
        outcome = _stripped_edit(item, config, entities, devices)
        if isinstance(outcome, Edit):
            edits.append(outcome)
        elif outcome is not None:
            by_hand[item.label] = outcome
    return CleanPlan(
        tuple(entities),
        tuple(devices),
        tuple(edits),
        tuple(HandEdit(label, reason) for label, reason in by_hand.items()),
    )


async def refresh(ha: HaClient, planned: CleanPlan) -> CleanPlan:
    """The plan again, from each config as it is now, so a change made since the plan is kept. Reads only."""
    edits, by_hand = [], list(planned.by_hand)
    for edit in planned.edits:
        if edit.item.kind == DASHBOARD:
            config = await ha.dashboard_config(edit.item.url_path)
            outcome = _stripped_edit(edit.item, config, planned.entities, planned.devices)
        else:
            outcome = await _edit(ha, edit.item, planned.entities, planned.devices)
        if isinstance(outcome, Edit):
            edits.append(outcome)
        elif outcome is not None:
            by_hand.append(HandEdit(edit.item.label, outcome))
    return CleanPlan(planned.entities, planned.devices, tuple(edits), tuple(by_hand))


async def save(ha: HaClient, planned: CleanPlan, folder: Path) -> CleanResult:
    """Save the planned edits. The caller holds the lock, and has shown the diffs and had them confirmed.

    Every config about to be saved is first written, as read, to
    `<folder>/ha-config.json`: what to POST back to undo the edit.
    """
    result = CleanResult(by_hand=list(planned.by_hand))
    if not planned.edits:
        return result
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / "ha-config.json"
    backup.write_text(
        json.dumps([{"kind": e.item.kind, "key": e.item.key, "config": e.before} for e in planned.edits], indent=2)
        + "\n"
    )
    result.backup = backup
    for edit in planned.edits:
        if edit.item.kind == DASHBOARD:
            await ha.save_dashboard_config(edit.item.url_path, edit.after)
        else:
            await ha.save_item_config(edit.item.kind, edit.item.key, edit.after)
        result.saved.append(edit)
    return result


async def _edit(ha: HaClient, item: Item, entities: Sequence[str], devices: Sequence[str]) -> Edit | str | None:
    config = await ha.item_config(item.kind, item.key)
    if config is None:
        return "not editable through HA's config API (defined in YAML?); edit it by hand"
    return _stripped_edit(item, config, entities, devices)


def _stripped_edit(item: Item, config: Any, entities: Sequence[str], devices: Sequence[str]) -> Edit | str | None:
    """The edit, the reason it needs a hand edit, or None when there is nothing to change."""
    outcome = strip(config, entities, devices, kind=item.kind)
    if outcome.refusal is not None:
        return outcome.refusal
    return Edit(item, config, outcome.config) if outcome.changed else None


def render(planned: CleanPlan, *, cleaning: bool) -> list[str]:
    """The References section of a removal's report."""
    if not planned.references:
        return ["References in live HA: none."]
    lines = [f"References in live HA ({planned.references})"]
    for edit in planned.edits:
        lines.append(f"  {edit.item.label}" + ("" if cleaning else ": --clean would edit it"))
        if cleaning:
            lines.extend(f"    {line}" for line in edit.diff().splitlines())
    for hand in planned.by_hand:
        lines.append(f"  {hand.label}: {hand.reason}")
    if not cleaning and planned.edits:
        lines.append("  Pass --clean to see the diffs, and --clean --apply to make them.")
    return lines
