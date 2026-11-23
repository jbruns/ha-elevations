"""Where the tool finds its credentials, lock and backups: a gitignored folder.

By default that is the repo's `.private/` (ADR 0004). Override it with
`--private-dir` or `INSTEON_PRIVATE_DIR`, for example from a git worktree,
which has no `.private/` of its own.

    <private>/eisy/iox.local.env   IOX_HOST, IOX_PORT, IOX_PROTO, IOX_USER_NAME,
                                   IOX_PASSWORD, and optionally IOX_CERT_SHA256
    <private>/ha-url               the Home Assistant URL
    <private>/ha-token             a long-lived access token
    <private>/insteon.lock         the lock
    <private>/insteon-backups/     backups taken before a change
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PRIVATE_DIR = REPO / ".private"
ENV_PRIVATE_DIR = "INSTEON_PRIVATE_DIR"


class ConfigError(RuntimeError):
    """A credentials file is missing or incomplete."""


@dataclass(frozen=True)
class IoxSettings:
    url: str
    user: str
    password: str  # gitleaks:allow (an annotation, not a value)
    cert_sha256: str | None

    def __repr__(self) -> str:  # never print the password
        return f"IoxSettings(url={self.url!r}, user={self.user!r})"


@dataclass(frozen=True)
class HaSettings:
    url: str
    token: str

    def __repr__(self) -> str:  # never print the token
        return f"HaSettings(url={self.url!r})"


@dataclass(frozen=True)
class Private:
    """The private folder and everything the tool keeps in it."""

    root: Path

    @classmethod
    def locate(cls, given: Path | None) -> Private:
        return cls(given or Path(os.environ.get(ENV_PRIVATE_DIR) or DEFAULT_PRIVATE_DIR))

    @property
    def lock_path(self) -> Path:
        return self.root / "insteon.lock"

    @property
    def backup_root(self) -> Path:
        return self.root / "insteon-backups"

    def iox(self) -> IoxSettings:
        path = self.root / "eisy" / "iox.local.env"
        values = _env_file(path)
        missing = [key for key in ("IOX_HOST", "IOX_USER_NAME", "IOX_PASSWORD") if not values.get(key)]
        if missing:
            raise ConfigError(f"{path} does not set {', '.join(missing)}")
        proto = values.get("IOX_PROTO") or "https"
        port = values.get("IOX_PORT") or "8443"
        return IoxSettings(
            url=f"{proto}://{values['IOX_HOST']}:{port}",
            user=values["IOX_USER_NAME"],
            password=values["IOX_PASSWORD"],
            cert_sha256=values.get("IOX_CERT_SHA256") or None,
        )

    def ha(self) -> HaSettings:
        return HaSettings(url=_one_line(self.root / "ha-url"), token=_one_line(self.root / "ha-token"))


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError as error:
        raise ConfigError(f"{path} not found; pass --private-dir or set {ENV_PRIVATE_DIR}") from error


def _one_line(path: Path) -> str:
    value = _read(path).strip()
    if not value:
        raise ConfigError(f"{path} is empty")
    return value


def _env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values
