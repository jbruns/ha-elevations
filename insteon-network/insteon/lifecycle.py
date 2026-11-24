"""`insteon programs --disable|--enable PROGRAM…`: retire IoX programs, reversibly.

Retiring a program (ADR 0012) is: disable it once its Home Assistant
replacement is live, watch for a week, then delete it. Disabling and enabling go
through REST (`/rest/programs/<id>/disable|enable`). Deleting is a one-off in
eisy-ui: IoX documents no call that deletes a program or a variable
(`research/iox-api.md` §3).

A dry run unless given `--apply`. Applying takes the lock and the IoX backup
first, then re-reads `/rest/programs` to check each program changed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from insteon.iox import IoxClient
from insteon.program import Program, folder_path, folders_above
from insteon.rails import IoxBackup, Rails
from insteon.resolve import ResolveError

_HEX_ID = re.compile(r"[0-9A-Fa-f]{1,4}")


@dataclass
class LifecycleResult:
    enabled: bool
    """The state asked for: True for `--enable`, False for `--disable`."""
    programs: tuple[Program, ...]
    """Every program named, directly or through its folder, with its state before."""
    listed: tuple[Program, ...]
    """Everything `/rest/programs` lists, for folder paths."""
    applied: bool = False
    unchanged: tuple[Program, ...] = ()
    """Programs `/rest/programs` still shows in their old state after applying."""
    iox_backup: Path | None = None

    @property
    def to_change(self) -> tuple[Program, ...]:
        return tuple(p for p in self.programs if p.enabled != self.enabled)

    @property
    def already(self) -> tuple[Program, ...]:
        return tuple(p for p in self.programs if p.enabled == self.enabled)


async def set_enabled(
    iox: IoxClient, rails: Rails, iox_backup: IoxBackup, names: Sequence[str], *, enabled: bool
) -> LifecycleResult:
    listed = tuple(await iox.programs())
    result = LifecycleResult(enabled, select_programs(names, listed), listed)
    if not rails.apply or not result.to_change:
        return result
    async with rails.changing("programs", [iox_backup]):
        result.iox_backup = iox_backup.path
        for program in result.to_change:
            await iox.set_program_enabled(program, enabled)
        result.applied = True
        after = {p.id: p.enabled for p in await iox.programs()}
        result.unchanged = tuple(p for p in result.to_change if after.get(p.id) != enabled)
    return result


def select_programs(names: Sequence[str], listed: Sequence[Program]) -> tuple[Program, ...]:
    """The programs `names` stand for, each once, in the order named.

    A name is a program or folder id in hex (`000A`, or `a`), or else its IoX
    name, in any case. A folder stands for every program in it and in its
    subfolders, in the order `/rest/programs` lists them.
    """
    chosen: dict[int, Program] = {}
    for name in names:
        program = _find(name, listed)
        if program.is_folder:
            members = [p for p in listed if not p.is_folder and program in folders_above(p, listed)]
        else:
            members = [program]
        chosen.update((p.id, p) for p in members if p.id not in chosen)
    return tuple(chosen.values())


def _find(name: str, listed: Sequence[Program]) -> Program:
    if _HEX_ID.fullmatch(name.strip()):
        number = int(name, 16)
        if (found := next((p for p in listed if p.id == number), None)) is not None:
            return found
    matches = [p for p in listed if p.name.casefold() == name.strip().casefold()]
    if not matches:
        raise ResolveError(f"no program or folder with id or name {name!r}")
    if len(matches) > 1:
        ids = ", ".join(p.iox_id for p in matches)
        raise ResolveError(f"{name!r} names {len(matches)} programs or folders; give an id: {ids}")
    return matches[0]


def render(result: LifecycleResult) -> str:
    verb = "enable" if result.enabled else "disable"
    listed = result.listed
    already = [f"Already {verb}d ({len(result.already)}):", *_labels(result.already, listed)] if result.already else []
    if not result.to_change:
        return "\n".join([f"Nothing to {verb}.", *already]) + "\n"
    if not result.applied:
        lines = [f"Would {verb} {_count(result.to_change)}:", *_labels(result.to_change, listed), *already]
        lines.append(f"Dry run: nothing changed. Pass --apply to {verb} them.")
        return "\n".join(lines) + "\n"
    changed = [p for p in result.to_change if p not in result.unchanged]
    lines = []
    if changed:
        confirms = "it" if len(changed) == 1 else "each"
        lines += [f"{verb.capitalize()}d {_count(changed)}; /rest/programs confirms {confirms}:", *_labels(changed, listed)]
    if result.unchanged:
        before = "disabled" if result.enabled else "enabled"
        lines.append(f"Not {verb}d: /rest/programs still shows {_count(result.unchanged)} {before}:")
        lines += _labels(result.unchanged, listed)
    lines += already
    lines.append(f"IoX backup: {result.iox_backup}")
    return "\n".join(lines) + "\n"


def _count(programs: Sequence[Program]) -> str:
    return f"{len(programs)} program{'' if len(programs) == 1 else 's'}"


def _labels(programs: Sequence[Program], listed: Sequence[Program]) -> list[str]:
    return [f"  {_label(p, listed)}" for p in programs]


def _label(program: Program, listed: Sequence[Program]) -> str:
    return f"{folder_path(program, listed)}{program.name}  [{program.iox_id}]"
