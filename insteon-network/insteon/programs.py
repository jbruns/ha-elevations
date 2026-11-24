"""`insteon programs`: export IoX programs and variables as readable text.

Read-only. Programs and variables migrate to Home Assistant by hand, program by
program, into the context that owns them; there is no translator (ADR 0012).
The export is the input to that plan.

- The program list, with enabled, run-at-startup and last-run state, comes from
  `/rest/programs`; variables from `/rest/vars`.
- Bodies (If, Then, Else) come from the D2D files over SSH. A statement in a
  shape the tool does not decode is printed raw, as `[not decoded]`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from insteon.address import ButtonAddress
from insteon.ha import Automation, HaClient, HaRegistry
from insteon.iox import IoxClient, IoxFiles
from insteon.network import Device, Network, Scene
from insteon.resolve import ha_entity_ids
from insteon.program import (
    Body,
    Command,
    Condition,
    NodeStatus,
    Program,
    SetVariable,
    Statement,
    Undecoded,
    Variable,
    VariableKind,
    VariableRef,
    Wait,
)

_OPS = {"IS": "is", "ISNOT": "is not"}
_ASSIGN = {"EQ": "="}
_COMMANDS = {"DON": "On", "DOF": "Off"}


@dataclass(frozen=True)
class Reference:
    """A Device or Scene a program names, and its entities in Home Assistant."""

    target: Device | Scene | str
    """A str is a node the Controller does not have, as the program writes it."""
    entity_ids: tuple[str, ...] = ()
    via: Scene | None = None
    """The Scene a Device is in, when the program names only the Scene."""


@dataclass(frozen=True)
class PossibleDuplicate:
    """An HA automation that references some of the same Devices as a program."""

    automation: Automation
    shared: tuple[str, ...]
    """The entity IDs both reference."""


@dataclass(frozen=True)
class ProgramsExport:
    network: Network
    programs: tuple[Program, ...]
    """Every program and folder, with its body where a D2D file holds one."""
    variables: tuple[Variable, ...]
    bodies_read: int
    """How many D2D files the export read."""
    unlisted: tuple[int, ...]
    """Ids of D2D files that match no program `/rest/programs` lists."""
    references: Mapping[int, tuple[Reference, ...]]
    duplicates: Mapping[int, tuple[PossibleDuplicate, ...]]

    def program(self, number: int) -> Program | None:
        return next((program for program in self.programs if program.id == number), None)

    def path(self, program: Program) -> str:
        """The folder path to a program or folder, such as `My Programs/Lights/`."""
        names: list[str] = []
        parent = program.parent if not program.is_folder else program.id
        seen: set[int] = set()
        while parent is not None and parent not in seen and (folder := self.program(parent)) is not None:
            seen.add(parent)
            names.append(folder.name)
            parent = folder.parent
        return "".join(f"{name}/" for name in reversed(names))

    def variable(self, ref: VariableRef) -> Variable | None:
        return next((v for v in self.variables if v.ref == ref), None)


async def export(iox: IoxClient, files: IoxFiles, ha: HaClient) -> ProgramsExport:
    """Read every program, its body and every variable, and what HA automates. Changes nothing."""
    network = await iox.network()
    bodies = await files.program_bodies()
    programs = tuple(replace(program, body=bodies.get(program.id)) for program in await iox.programs())
    registry = await ha.registry()
    automations = await ha.automations()
    references = {
        program.id: program_references(program.body, network, registry) for program in programs if program.body
    }
    return ProgramsExport(
        network=network,
        programs=programs,
        variables=tuple(await iox.variables()),
        bodies_read=len(bodies),
        unlisted=tuple(sorted(set(bodies) - {program.id for program in programs})),
        references=references,
        duplicates={
            number: possible_duplicates(found, automations, registry) for number, found in references.items()
        },
    )


def program_references(body: Body, network: Network, registry: HaRegistry) -> tuple[Reference, ...]:
    """The Devices and Scenes a body names, in order of first use.

    A Scene stands for its members too: a command to it drives each of them.
    Members the body does not name itself follow the Scene.
    """
    named: list[Device | Scene | str] = []
    for node in body.nodes:
        button = ButtonAddress.try_parse(node)
        known = network.device_of(button) if button else network.scene(node)
        named.append(known or node)
    found: dict[object, Reference] = {}

    def key(target: Device | Scene | str) -> object:
        return target if isinstance(target, str) else target.address

    def add(target: Device | Scene | str, via: Scene | None = None) -> None:
        if key(target) not in found:
            entity_ids = () if isinstance(target, str) else ha_entity_ids(target, network, registry)
            found[key(target)] = Reference(target, entity_ids, via)

    direct = {key(target) for target in named}
    for target in named:
        add(target)
        if isinstance(target, Scene):
            for member in (*target.controllers, *target.responders):
                device = network.device_of(member)
                if device is not None and device.address not in direct:
                    add(device, via=target)
    return tuple(found.values())


def possible_duplicates(
    references: Sequence[Reference], automations: Sequence[Automation], registry: HaRegistry
) -> tuple[PossibleDuplicate, ...]:
    """Automations that reference any entity of the program's Devices and Scenes.

    Either side may only watch a Device (in an If, or an automation's trigger),
    so a match is a possible duplicate to check by hand, not a verdict.
    """
    ours = {entity_id for reference in references for entity_id in reference.entity_ids}
    found = []
    for automation in automations:
        theirs = set(automation.entities)
        for device_id in automation.devices:
            theirs.update(entity["entity_id"] for entity in registry.entities_of(device_id))
        if shared := ours & theirs:
            found.append(PossibleDuplicate(automation, tuple(sorted(shared))))
    return tuple(found)


def render(result: ProgramsExport) -> str:
    folders = sorted((p for p in result.programs if p.is_folder), key=result.path)
    programs = [p for p in result.programs if not p.is_folder]
    undecoded = sum(
        isinstance(statement, Undecoded) for p in result.programs if p.body for statement in p.body.statements
    )
    lines = [
        f"IoX programs: {len(programs)} programs in {len(folders)} folders",
        f"Bodies: {result.bodies_read} D2D files read over SSH; "
        + (f"{undecoded} statements not decoded, shown raw as [not decoded]" if undecoded else "every statement decoded"),
    ]
    if result.unlisted:
        count = len(result.unlisted)
        lines.append(
            f"{count} D2D file{' matches' if count == 1 else 's match'} no listed program: "
            + ", ".join(f"{number:04X}" for number in result.unlisted)
        )
    lines.append("")
    folder_ids = {folder.id for folder in folders}
    groups: list[tuple[str, Program | None]] = [(f"{result.path(f)}  [{f.iox_id}]", f) for f in folders]
    if any(p.parent not in folder_ids for p in programs):
        groups.append(("(no folder)", None))
    for heading, folder in groups:
        lines.append(heading)
        if folder is not None and folder.body is not None and folder.body.conditions:
            lines += _indent(_entry(folder, result), 2)
        members = (p for p in programs if (p.parent == folder.id if folder else p.parent not in folder_ids))
        for program in sorted(members, key=lambda p: p.name):
            lines.append(f"  {program.name}  [{program.iox_id}]  {_state(program)}")
            lines += _indent(_entry(program, result), 4)
        lines.append("")
    lines += _variables(result)
    return "\n".join(lines).rstrip("\n") + "\n"


def _variables(result: ProgramsExport) -> list[str]:
    kinds = {kind: [v for v in result.variables if v.kind == kind] for kind in VariableKind}
    lines = ["IoX variables: " + ", ".join(f"{len(found)} {kind.name.lower()}" for kind, found in kinds.items())]
    for kind, found in kinds.items():
        lines.append(f"  {kind.name.capitalize()}")
        for variable in found:
            users = sorted(
                p.name for p in result.programs if p.body is not None and variable.ref in p.body.variables
            )
            facts = [f"value {variable.value}", f"init {variable.init}"]
            if variable.precision:
                facts.append(f"precision {variable.precision}")
            if variable.changed:
                facts.append(f"changed {variable.changed}")
            lines.append(
                f"    {variable.id}  {variable.name}  {', '.join(facts)}; used by {', '.join(users) or 'no program'}"
            )
        if not found:
            lines.append("    none")
    return lines


def _entry(program: Program, result: ProgramsExport) -> list[str]:
    """A program's body, the Devices it names, and the HA automations that may duplicate it."""
    if program.body is None:
        return ["body: no D2D file, so its Devices are unknown"]
    lines = _body(program.body, result)
    references = result.references.get(program.id, ())
    lines += ["Devices:", *_indent([_reference(r) for r in references], 2)] if references else ["Devices: none"]
    duplicates = result.duplicates.get(program.id, ())
    if duplicates:
        lines.append("Possible duplicates in HA (automations that reference the same Devices):")
        lines += _indent(
            [f"{d.automation.entity_id} ({d.automation.name}): {', '.join(d.shared)}" for d in duplicates], 2
        )
    else:
        lines.append("No HA automation references these Devices.")
    return lines


def _reference(reference: Reference) -> str:
    target = reference.target
    if isinstance(target, str):
        return f"unknown Device or Scene ({target}): not on the Controller"
    name = f"Scene '{target.name}' ({target.address})" if isinstance(target, Scene) else f"'{target.name}' ({target.address})"
    if reference.via is not None:
        name += f", in Scene {reference.via.address}"
    return f"{name}: {', '.join(reference.entity_ids) or 'no HA entity'}"


def _state(program: Program) -> str:
    parts = ["enabled" if program.enabled else "disabled"]
    if program.run_at_startup:
        parts.append("runs at startup")
    if program.status is not None:
        parts.append(f"If last {'true' if program.status else 'false'}")
    parts.append(f"last run {program.last_run}" if program.last_run else "never run")
    return ", ".join(parts)


def _body(body: Body, result: ProgramsExport) -> list[str]:
    lines = ["If"]
    lines += _indent([_statement(s, result, first=i == 0) for i, s in enumerate(body.conditions)] or ["- No Conditions -"], 2)
    lines.append("Then")
    lines += _indent([_statement(s, result) for s in body.then] or ["- No Actions -"], 2)
    lines.append("Else")
    lines += _indent([_statement(s, result) for s in body.otherwise] or ["- No Actions -"], 2)
    if body.comment:
        lines.append(f"Comment: {body.comment}")
    return lines


def _statement(statement: Statement, result: ProgramsExport, *, first: bool = False) -> str:
    match statement:
        case Condition(joiner=joiner, left=left, op=op, right=right):
            line = f"{_status(left, result)} {_OPS.get(op, op.lower())} {_variable(right, result)}"
            return line if first else f"{joiner.capitalize()} {line}"
        case SetVariable(variable=variable, op=op, value=value):
            return f"{_variable(variable, result)} {_ASSIGN.get(op, op)} {_status(value, result)}"
        case Wait(seconds=seconds):
            return f"Wait {seconds} second{'' if seconds == 1 else 's'}"
        case Command(command=command, node=node, parameters=parameters):
            words = [f"Set {_node(node, result)}", _COMMANDS.get(command, command)]
            words += [_variable(parameter, result) for parameter in parameters]
            return " ".join(words)
        case Undecoded(xml=xml, whole_file=True):
            return f"[file not parsed] {xml}"
        case Undecoded(xml=xml):
            return f"[not decoded] {xml}"


def _status(status: NodeStatus, result: ProgramsExport) -> str:
    what = "Status" if status.property == "ST" else f"Property {status.property}"
    return f"{what} {_node(status.node, result)}"


def _node(node: str, result: ProgramsExport) -> str:
    """A node as the Admin Console names it: a Button by name, or a Scene."""
    button_address = ButtonAddress.try_parse(node)
    if button_address is not None:
        button = result.network.button(button_address)
        return f"'{button.name}' ({button_address})" if button else f"unknown Button ({button_address})"
    scene = result.network.scene(node)
    return f"Scene '{scene.name}' ({node})" if scene else f"unknown Device or Scene ({node})"


def _variable(ref: VariableRef, result: ProgramsExport) -> str:
    variable = result.variable(ref)
    if variable is None:
        return f"${ref.kind.name.lower()} variable {ref.id}"
    return f"${variable.name}"


def _indent(lines: list[str], spaces: int) -> list[str]:
    return [" " * spaces + line for line in lines]
