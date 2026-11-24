"""The `insteon` command. Run it as `uv run insteon`.

Every command that changes something is a dry run unless given `--apply`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from insteon import add as add_command
from insteon import audit as audit_command
from insteon import inventory as inventory_command
from insteon import lifecycle as lifecycle_command
from insteon import programs as programs_command
from insteon import prune as prune_command
from insteon import refs as refs_command
from insteon import remove as remove_command
from insteon import replace as replace_command
from insteon import restore as restore_command
from insteon import scene as scene_command
from insteon.config import ENV_PRIVATE_DIR, REPO, ConfigError, Private
from insteon.ha import HaClient, HaError
from insteon.iox import IoxClient, IoxError, IoxFiles
from insteon.rails import IoxBackup, LockHeld, Rails
from insteon.resolve import ResolveError, needs_home_assistant

# Seconds to wait for the IoX backup: all of CONF, over SSH.
_BACKUP_TIMEOUT = 300


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insteon",
        description="Manage the Insteon network on IoX, and Reconcile Home Assistant to it.",
    )
    parser.add_argument(
        "--private-dir",
        type=Path,
        help=f"the gitignored folder with credentials, lock and backups (default: the repo's .private/, or ${ENV_PRIVATE_DIR})",
    )
    parser.add_argument("--ha-ipv4", action="store_true", help="reach Home Assistant over IPv4 only")
    commands = parser.add_subparsers(dest="command", required=True)

    inventory = commands.add_parser(
        "inventory", help="Devices, Buttons, Scenes, and the Modem's Link count against its capacity"
    )
    inventory.add_argument(
        "devices", nargs="*", metavar="DEVICE", help="only these: Insteon Address, IoX name or HA entity ID"
    )
    inventory.add_argument(
        "--modem-capacity",
        type=int,
        default=inventory_command.DEFAULT_CAPACITY,
        help="Links the Modem holds before status reports fail (default: %(default)s)",
    )
    inventory.add_argument(
        "--no-modem-links", action="store_true", help="skip reading the Modem's Link table (about 2 records/s)"
    )

    prune = commands.add_parser("prune", help="remove Stale HA Devices")
    prune.add_argument("--apply", action="store_true", help="remove them; without it, only list them")

    programs = commands.add_parser(
        "programs",
        help="export IoX programs and variables as text, flagging HA automations on the same Devices",
    )
    _program_lifecycle_options(programs)

    audit = commands.add_parser("audit", help="Link Audit: differences, Stale Links, Unresponsive Devices (read-only)")
    audit.add_argument(
        "devices", nargs="*", metavar="DEVICE", help="only these (default: every Device but Battery and disabled ones)"
    )
    audit.add_argument(
        "--timeout",
        type=float,
        default=audit_command.DEFAULT_TIMEOUT,
        help="seconds to wait for one Device's Links before calling it Unresponsive (default: %(default)s)",
    )
    audit.add_argument("--no-modem-links", action="store_true", help="skip reading the Modem's Link table")

    refs = commands.add_parser("refs", help="every Reference to a Device's entities, in HA and the repo (read-only)")
    refs.add_argument("device", metavar="DEVICE", help="Insteon Address, IoX name or HA entity ID")
    refs.add_argument(
        "--repo", type=Path, default=REPO, help="the checkout whose *.local.yaml overlays to read (default: %(default)s)"
    )

    apply_help = "make the change (after backing up IoX); without it, only show the plan"
    restore = commands.add_parser("restore", help="Restore a Device's or the Modem's Links from the Controller's database")
    restore.add_argument("device", metavar="DEVICE|modem", help="Insteon Address, IoX name or HA entity ID, or `modem`")
    restore.add_argument("--apply", action="store_true", help=apply_help)

    remove = commands.add_parser(
        "remove", help="leave Scenes, write, remove, Restore former Scene Peers and the Modem, then Reconcile HA"
    )
    remove.add_argument("device", metavar="DEVICE", help="Insteon Address, IoX name or HA entity ID")
    remove.add_argument("--clean", action="store_true", help="also take its References out of live HA (a diff first)")
    remove.add_argument("--apply", action="store_true", help=apply_help)
    remove.add_argument(
        "--repo", type=Path, default=REPO, help="the checkout whose References to list (default: %(default)s)"
    )
    remove.add_argument(
        "--timeout",
        type=float,
        default=audit_command.DEFAULT_TIMEOUT,
        help="seconds to wait for a Device's Links before calling it Unresponsive (default: %(default)s)",
    )
    remove.add_argument(
        "--no-modem-links", action="store_true", help="skip reading the Modem's Links (and so its Restore)"
    )

    add = commands.add_parser("add", help="add a Device by Insteon Address, erasing its Links, then Reconcile HA")
    add.add_argument("address", metavar="ADDRESS", help="the Insteon Address printed on the Device")
    add.add_argument("name", metavar="NAME", help="its IoX name")
    add.add_argument(
        "--from-links", action="store_true", help="keep its Links and build Scenes from them, instead of erasing them"
    )
    add.add_argument("--battery", action="store_true", help="a Battery Device: prompt to hold its Set button first")
    add.add_argument("--apply", action="store_true", help=apply_help)

    replace = commands.add_parser(
        "replace", help="IoX's Replace of OLD with NEW (already added), then Reconcile HA keeping OLD's entity IDs"
    )
    replace.add_argument("old", metavar="OLD", help="the Device to replace: Insteon Address, IoX name or HA entity ID")
    replace.add_argument("new", metavar="NEW", help="the Device replacing it, already added to IoX and HA")
    replace.add_argument("--apply", action="store_true", help=apply_help)
    replace.add_argument(
        "--repo", type=Path, default=REPO, help="the checkout whose References to list (default: %(default)s)"
    )

    scene = commands.add_parser("scene", help="create and delete Scenes, and edit them Button by Button")
    actions = scene.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="create an empty Scene")
    create.add_argument("name", metavar="NAME")
    create.add_argument("--address", help="its Scene address (default: one past the highest in use)")
    delete = actions.add_parser("delete", help="delete a Scene")
    delete.add_argument("scene", metavar="SCENE", help="Scene address or IoX name")
    join = actions.add_parser("add", help="add a Button to a Scene")
    join.add_argument("scene", metavar="SCENE", help="Scene address or IoX name")
    join.add_argument("button", metavar="BUTTON", help="Button address (AA BB CC 3), Button name, or a Device")
    role = join.add_mutually_exclusive_group(required=True)
    role.add_argument("--controller", action="store_true", help="as a Controller Button (it controls at most one Scene)")
    role.add_argument("--responder", action="store_true", help="as a Responder Button")
    leave = actions.add_parser("remove", help="take a Button out of a Scene")
    leave.add_argument("scene", metavar="SCENE", help="Scene address or IoX name")
    leave.add_argument("button", metavar="BUTTON", help="Button address, Button name, or a Device")
    level = actions.add_parser("level", help="set a Responder Button's on level in a Scene")
    level.add_argument("scene", metavar="SCENE", help="Scene address or IoX name")
    level.add_argument("button", metavar="RESPONDER", help="the Responder Button")
    level.add_argument("percent", type=float, metavar="PERCENT", help="on level, 0 to 100")
    level.add_argument("--ramp", type=int, help="ramp rate, an index from 0 to 31 into Insteon's table")
    level.add_argument(
        "--controller", dest="from_button", metavar="BUTTON", help="only when this Controller Button turns it on"
    )
    for action in (create, delete, join, leave, level):
        action.add_argument("--apply", action="store_true", help=apply_help)
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "programs" and args.apply and not (args.disable or args.enable):
        parser.error("programs: --apply needs --disable or --enable; the export changes nothing")
    private = Private.locate(args.private_dir)
    try:
        if args.command == "inventory":
            await _inventory(args, private)
        elif args.command == "prune":
            await _prune(args, private)
        elif args.command == "programs":
            await _programs(args, private)
        elif args.command == "audit":
            await _audit(args, private)
        elif args.command == "refs":
            await _refs(args, private)
        elif args.command == "restore":
            await _restore(args, private)
        elif args.command == "remove":
            await _remove(args, private)
        elif args.command == "add":
            await _add(args, private)
        elif args.command == "replace":
            await _replace(args, private)
        elif args.command == "scene":
            await _scene(args, private)
    except (ConfigError, IoxError, HaError, ResolveError, LockHeld, refs_command.RepoError) as error:
        print(f"insteon: error: {error}", file=sys.stderr)
        return 1
    return 0


def _iox(private: Private) -> IoxClient:
    settings = private.iox()
    return IoxClient(settings.url, settings.user, settings.password, cert_sha256=settings.cert_sha256)


def _ha(private: Private, args: argparse.Namespace) -> HaClient:
    settings = private.ha()
    return HaClient(settings.url, settings.token, ipv4_only=args.ha_ipv4)


def _rails(private: Private, apply: bool) -> Rails:
    return Rails(apply=apply, lock_path=private.lock_path, backup_root=private.backup_root)


def _iox_files(private: Private, *, timeout: float = 60) -> IoxFiles:
    ssh = private.iox_ssh()
    return IoxFiles(ssh.host, ssh.user, ssh.password, port=ssh.port, known_hosts=ssh.known_hosts, timeout=timeout)


def _iox_backup(private: Private) -> IoxBackup:
    """The IoX backup every command that changes IoX takes first. Needs the SSH login, even for a dry run."""
    return IoxBackup(_iox_files(private, timeout=_BACKUP_TIMEOUT), private.iox_backup_root)


async def _inventory(args: argparse.Namespace, private: Private) -> None:
    async with _iox(private) as iox:
        registry = None
        if any(needs_home_assistant(name) for name in args.devices):
            async with _ha(private, args) as ha:
                registry = await ha.registry()
        report = await inventory_command.inventory(
            iox,
            _rails(private, apply=False),
            capacity=args.modem_capacity,
            read_modem_links=not args.no_modem_links,
            names=args.devices,
            registry=registry,
        )
    print(inventory_command.render(report), end="")


async def _prune(args: argparse.Namespace, private: Private) -> None:
    async with _iox(private) as iox, _ha(private, args) as ha:
        result = await prune_command.prune(iox, ha, _rails(private, apply=args.apply))
    print(prune_command.render(result, applying=args.apply), end="")


async def _programs(args: argparse.Namespace, private: Private) -> None:
    if args.disable or args.enable:
        await _program_lifecycle(args, private)
        return
    files = _iox_files(private)
    async with _iox(private) as iox, _ha(private, args) as ha:
        result = await programs_command.export(iox, files, ha)
    print(programs_command.render(result), end="")


# insteon programs --disable/--enable ---------------------------------------------


def _program_lifecycle_options(programs: argparse.ArgumentParser) -> None:
    action = programs.add_mutually_exclusive_group()
    for flag, verb in (("--disable", "disable"), ("--enable", "enable")):
        action.add_argument(
            flag,
            nargs="+",
            metavar="PROGRAM",
            help=f"{verb} these instead of exporting: program or folder ids (000A) or IoX names; a folder means every program in it",
        )
    programs.add_argument(
        "--apply", action="store_true", help="with --disable or --enable: make the change (after backing up IoX)"
    )


async def _program_lifecycle(args: argparse.Namespace, private: Private) -> None:
    backup = _iox_backup(private)
    async with _iox(private) as iox:
        result = await lifecycle_command.set_enabled(
            iox, _rails(private, args.apply), backup, args.enable or args.disable, enabled=bool(args.enable)
        )
    print(lifecycle_command.render(result), end="")
    if result.unchanged:
        count = len(result.unchanged)
        raise IoxError(f"{count} program{' did' if count == 1 else 's did'} not change; run the same command again to retry")


async def _audit(args: argparse.Namespace, private: Private) -> None:
    async with _iox(private) as iox:
        registry = None
        if any(needs_home_assistant(name) for name in args.devices):
            async with _ha(private, args) as ha:
                registry = await ha.registry()
        report = await audit_command.audit(
            iox,
            _rails(private, apply=False),
            names=args.devices,
            registry=registry,
            read_modem_links=not args.no_modem_links,
            timeout=args.timeout,
            prompt=_ask,
            progress=_progress,
        )
    print(audit_command.render(report), end="")


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _ask(message: str) -> str:
    print(message, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline()


async def _refs(args: argparse.Namespace, private: Private) -> None:
    async with _iox(private) as iox, _ha(private, args) as ha:
        report = await refs_command.refs(iox, ha, args.device, repo=args.repo)
    print(refs_command.render(report), end="")


async def _restore(args: argparse.Namespace, private: Private) -> None:
    backup = _iox_backup(private)
    async with _iox(private) as iox:
        registry = None
        if needs_home_assistant(args.device):
            async with _ha(private, args) as ha:
                registry = await ha.registry()
        result = await restore_command.restore(
            iox, _rails(private, args.apply), backup, args.device, registry=registry, prompt=_ask
        )
    print(restore_command.render(result), end="")


async def _remove(args: argparse.Namespace, private: Private) -> None:
    backup = _iox_backup(private)
    async with _iox(private) as iox, _ha(private, args) as ha:
        result = await remove_command.remove(
            iox,
            ha,
            _rails(private, args.apply),
            backup,
            args.device,
            repo=args.repo,
            clean=args.clean,
            read_modem_links=not args.no_modem_links,
            timeout=args.timeout,
            prompt=_ask,
            progress=_progress,
        )
    print(remove_command.render(result), end="")


async def _add(args: argparse.Namespace, private: Private) -> None:
    backup = _iox_backup(private)
    async with _iox(private) as iox, _ha(private, args) as ha:
        result = await add_command.add(
            iox,
            ha,
            _rails(private, args.apply),
            backup,
            args.address,
            args.name,
            from_links=args.from_links,
            battery=args.battery,
            prompt=_ask,
            progress=_progress,
        )
    print(add_command.render(result), end="")


async def _replace(args: argparse.Namespace, private: Private) -> None:
    backup = _iox_backup(private)
    async with _iox(private) as iox, _ha(private, args) as ha:
        result = await replace_command.replace(
            iox, ha, _rails(private, args.apply), backup, args.old, args.new, repo=args.repo, prompt=_ask, progress=_progress
        )
    print(replace_command.render(result), end="")


async def _scene(args: argparse.Namespace, private: Private) -> None:
    backup = _iox_backup(private)
    async with _iox(private) as iox, _ha(private, args) as ha:
        registry = await ha.registry()
        if args.action == "create":
            planner = scene_command.create(args.name, args.address)
        elif args.action == "delete":
            planner = scene_command.delete(args.scene)
        elif args.action == "add":
            planner = scene_command.add_button(args.scene, args.button, controller=args.controller, registry=registry)
        elif args.action == "remove":
            planner = scene_command.remove_button(args.scene, args.button, registry=registry)
        else:
            planner = scene_command.level(
                args.scene, args.button, args.percent, ramp=args.ramp, controller_name=args.from_button, registry=registry
            )
        result = await scene_command.scene(
            iox, ha, _rails(private, args.apply), backup, planner, prompt=_ask, progress=_progress
        )
    print(scene_command.render(result), end="")


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
