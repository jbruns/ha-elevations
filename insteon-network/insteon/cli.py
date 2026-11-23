"""The `insteon` command. Run it as `uv run insteon`.

Every command that changes something is a dry run unless given `--apply`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from insteon import inventory as inventory_command
from insteon import prune as prune_command
from insteon.config import ENV_PRIVATE_DIR, ConfigError, Private
from insteon.ha import HaClient, HaError
from insteon.iox import IoxClient, IoxError
from insteon.rails import LockHeld, Rails
from insteon.resolve import ResolveError, needs_home_assistant


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
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    private = Private.locate(args.private_dir)
    try:
        if args.command == "inventory":
            await _inventory(args, private)
        elif args.command == "prune":
            await _prune(args, private)
    except (ConfigError, IoxError, HaError, ResolveError, LockHeld) as error:
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


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
