# Insteon Network

The `insteon` command manages the home's Insteon network on the Controller (an eisy running IoX), and Reconciles Home Assistant to it. Vocabulary is in [CONTEXT.md](./CONTEXT.md). IoX manages only the Insteon network (ADR 0012), and the tool talks to it over local SOAP and REST, not the cloud (ADR 0013). The spec is issue #79. The IoX and isy994 facts the code relies on are in [research/](./research/README.md).

## Run it

From the repo root:

```sh
uv run insteon inventory                 # Devices, Buttons, Scenes, Modem Links, misconfiguration
uv run insteon inventory 1A.2B.3C        # only some Devices, and the Scenes they are in
uv run insteon prune                     # list Stale HA Devices (a dry run)
uv run insteon prune --apply             # remove them
```

`insteon-network/` is a member of the repo's uv workspace, and the root project depends on it, so `uv sync` installs the command. The Python package is `insteon`, because a package name can't contain a hyphen.

A DEVICE argument is an Insteon Address in any format (`1A.2B.3C`, `1a2b3c`, `AA B C 1` as `/rest/nodes` writes it), the IoX name of a Device or of one of its Buttons, or the ID of a Home Assistant entity from the isy994 integration.

## Credentials

The tool reads its credentials from a gitignored folder, never from the repo (ADR 0004). By default that is the repo's `.private/`. A git worktree has no `.private/` of its own, so pass the main checkout's with `--private-dir`, or set `INSTEON_PRIVATE_DIR`.

| File | Holds |
| --- | --- |
| `eisy/iox.local.env` | `IOX_HOST`, `IOX_PORT` (8443), `IOX_PROTO` (https), `IOX_USER_NAME`, `IOX_PASSWORD`; optionally `IOX_CERT_SHA256` |
| `ha-url` | The Home Assistant URL, such as `https://ha.example.com` |
| `ha-token` | A long-lived access token |

The Controller's certificate is self-signed. Set `IOX_CERT_SHA256` to its SHA-256 fingerprint to pin it. Without that, the tool warns and skips verification. If Home Assistant is slow to answer over IPv6, pass `--ha-ipv4`.

## Commands

### `inventory`

This command is read-only. It lists:

- each Device with its Buttons and IoX folder, and whether it is disabled or IoX reports a communication error;
- each Scene with its Controller and Responder Buttons;
- the Modem's address and firmware, and its Link count against capacity. Capacity is about 900 Links by default; change it with `--modem-capacity`. The tool warns at 80%.
- nodes that are not Insteon, such as Z-Wave, Zigbee or node servers, as misconfiguration (ADR 0012).

Reading the Modem's Link table takes about half a second per record, so a few minutes for a full table. It holds the lock, since the Modem serves one Link operation at a time. Skip it with `--no-modem-links`.

### `prune`

Finds Stale HA Devices: isy994 devices in Home Assistant whose identifier (`<uuid>_<address>`) names nothing the Controller has. Those include devices from another Controller (a different uuid). The hub and the Variables and Networking devices of the current Controller are kept. isy994's own removal check accepts any device, live ones too, so the tool decides from `/rest/nodes` what is stale and never relies on that check.

A Stale HA Device that still has entities is held back, following the Reconcile order in [research/pyisy-and-ha-isy994.md](./research/pyisy-and-ha-isy994.md) §3: reload the isy994 integration, which removes stale entities, then prune again. Removing a device that still has entities would delete them, and any References to them would break silently. With `--apply`, prune removes each Stale HA Device with `config/device_registry/remove_config_entry`.

## Rails

Every command that changes something follows these rails (`insteon/rails.py`):

- **Dry run** unless given `--apply`.
- **Lock**: `<private>/insteon.lock` (an `flock`). A second command fails at once and names the holder.
- **Backups** go to `<private>/insteon-backups/<time>-<command>/` before any change. Here is what's real today:
  - Home Assistant: the device registry, entity registry and isy994 config entries, as JSON. This is a record to re-create from by hand, not a Home Assistant backup.
  - IoX: not built yet. No command changes IoX so far. The first one that does must add the full backup over SSH (`/var/isy/FILES/CONF/`) as a `Backup`.

## Code

| Module | Does |
| --- | --- |
| `insteon/address.py` | Insteon Addresses and Button addresses, in every format |
| `insteon/network.py` | Devices, Buttons, Scenes, Links and the Modem, with no wire formats |
| `insteon/iox.py` | All of IoX: REST, the SOAP envelope, the event stream, Link-table reads (ADR 0013) |
| `insteon/ha.py` | Home Assistant's WebSocket API |
| `insteon/resolve.py` | DEVICE argument → Device |
| `insteon/rails.py` | Dry run, lock and backups |
| `insteon/config.py` | Finds the private folder and reads the credentials |
| `insteon/inventory.py`, `insteon/prune.py` | The commands |
| `insteon/cli.py` | The `insteon` entry point |

## Tests

```sh
uv run pytest insteon-network
```

The tests run the real clients against a fake IoX and a fake Home Assistant on localhost (`tests/insteon_fakes.py`). These serve made-up responses in IoX 6.0.5's shapes, with placeholder names, addresses and uuid. Real captured responses stay in `.private/`.

## After re-adding isy994

Home Assistant gives each isy994 device the area named after its IoX folder. Check those areas by hand, and fix any that don't match the room.
