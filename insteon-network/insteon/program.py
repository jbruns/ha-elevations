"""IoX programs and variables, which migrate to Home Assistant by hand (ADR 0012).

These types know nothing about IoX's wire formats; `insteon.iox` builds them.
A program body keeps every statement it holds: one the tool cannot decode stays
as its raw XML in an `Undecoded`, so an export never drops logic silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum


class VariableKind(IntEnum):
    """IoX's variable types, as numbered in its REST paths."""

    INTEGER = 1
    STATE = 2


@dataclass(frozen=True)
class VariableRef:
    kind: VariableKind
    id: int


@dataclass(frozen=True)
class NodeStatus:
    """A node's property, such as a Button's status (`ST`)."""

    node: str
    """The node as IoX writes it: a Button address (`AA B C 1`) or a Scene address."""
    property: str


@dataclass(frozen=True)
class Condition:
    """One If line: `<joiner> <left> <op> <right>`."""

    joiner: str
    """`and` or `or`, joining it to the line before."""
    left: NodeStatus
    op: str
    right: VariableRef


@dataclass(frozen=True)
class SetVariable:
    variable: VariableRef
    op: str
    value: NodeStatus


@dataclass(frozen=True)
class Wait:
    seconds: int


@dataclass(frozen=True)
class Command:
    """A command sent to a node, such as `DON` to a Scene."""

    command: str
    node: str
    parameters: tuple[VariableRef, ...]


@dataclass(frozen=True)
class Undecoded:
    """A statement in a shape the tool does not decode, kept as its raw XML."""

    xml: str
    nodes: tuple[str, ...] = ()
    """The nodes its XML names, so its Devices still count."""
    whole_file: bool = False
    """The whole D2D file, which did not parse as XML."""


Statement = Condition | SetVariable | Wait | Command | Undecoded


@dataclass(frozen=True)
class Body:
    """A program's If, Then and Else, from its D2D file."""

    conditions: tuple[Statement, ...]
    then: tuple[Statement, ...]
    otherwise: tuple[Statement, ...]
    comment: str

    @property
    def statements(self) -> tuple[Statement, ...]:
        return (*self.conditions, *self.then, *self.otherwise)

    @property
    def nodes(self) -> tuple[str, ...]:
        """Every node the body names, as IoX writes it, in order of first use."""
        found: dict[str, None] = {}
        for statement in self.statements:
            match statement:
                case Condition(left=NodeStatus(node=node)) | SetVariable(value=NodeStatus(node=node)):
                    found[node] = None
                case Command(node=node):
                    found[node] = None
                case Undecoded(nodes=nodes):
                    found.update(dict.fromkeys(nodes))
        return tuple(found)

    @property
    def variables(self) -> frozenset[VariableRef]:
        found: set[VariableRef] = set()
        for statement in self.statements:
            match statement:
                case Condition(right=variable) | SetVariable(variable=variable):
                    found.add(variable)
                case Command(parameters=parameters):
                    found.update(parameters)
        return frozenset(found)


@dataclass(frozen=True)
class Program:
    """One IoX program or program folder, as `/rest/programs` lists it."""

    id: int
    name: str
    parent: int | None
    is_folder: bool
    enabled: bool | None
    """None for a folder, which IoX does not enable or disable."""
    run_at_startup: bool | None
    status: bool | None
    """Whether its If was last true."""
    last_run: str | None
    last_finish: str | None
    body: Body | None = None
    """From its D2D file; None until read."""

    @property
    def iox_id(self) -> str:
        """The id as IoX's REST paths and D2D file names write it, such as `000A`."""
        return f"{self.id:04X}"


def folders_above(program: Program, programs: Sequence[Program]) -> tuple[Program, ...]:
    """The folders that hold a program or folder, nearest first."""
    by_id = {p.id: p for p in programs}
    found: list[Program] = []
    parent, seen = program.parent, {program.id}
    while parent is not None and parent not in seen and (folder := by_id.get(parent)) is not None:
        seen.add(parent)
        found.append(folder)
        parent = folder.parent
    return tuple(found)


def folder_path(program: Program, programs: Sequence[Program]) -> str:
    """The folder path to a program or folder, such as `My Programs/Lights/`.

    A folder's path ends with its own name; a program's ends with its folder's.
    """
    folders = (program, *folders_above(program, programs)) if program.is_folder else folders_above(program, programs)
    return "".join(f"{folder.name}/" for folder in reversed(folders))


@dataclass(frozen=True)
class Variable:
    kind: VariableKind
    id: int
    name: str
    value: int
    init: int
    """The value IoX sets at startup."""
    precision: int
    changed: str | None
    """When IoX last changed the value, as IoX writes it."""

    @property
    def ref(self) -> VariableRef:
        return VariableRef(self.kind, self.id)
