# Insteon Network

The `insteon` command manages the home's Insteon network on the Controller (an eisy running IoX), and Reconciles Home Assistant to it. Vocabulary is in [CONTEXT.md](./CONTEXT.md). IoX manages only the Insteon network (ADR 0012), and the tool talks to it over local SOAP and REST, not the cloud (ADR 0013). The spec is issue #79. The IoX and isy994 facts the code relies on are in [research/](./research/README.md).

## Run it

From the repo root:

```sh
uv run insteon inventory                 # Devices, Buttons, Scenes, Modem Links, misconfiguration
uv run insteon inventory 1A.2B.3C        # only some Devices, and the Scenes they are in
uv run insteon prune                     # list Stale HA Devices (a dry run)
uv run insteon prune --apply             # remove them
uv run insteon programs > .private/iox-programs.txt   # IoX programs and variables as text
uv run insteon audit                     # the Link Audit of every Device and the Modem (minutes)
uv run insteon audit 1A.2B.3C            # only some Devices
uv run insteon refs light.example        # every Reference to a Device's entities
uv run insteon restore 1A.2B.3C          # Restore a Device's Links (a dry run; add --apply)
uv run insteon restore modem             # Restore the Modem's Links
uv run insteon remove 1A.2B.3C --clean   # the removal plan, and the diffs to live HA
uv run insteon add 1A.2B.3C "Example"    # add a Device, erasing its Links
uv run insteon scene add "Example Scene" "1A 2B 3C 3" --responder
```

Every command that changes something is a dry run until given `--apply`.

`insteon-network/` is a member of the repo's uv workspace, and the root project depends on it, so `uv sync` installs the command. The Python package is `insteon`, because a package name can't contain a hyphen.

A DEVICE argument is an Insteon Address in any format (`1A.2B.3C`, `1a2b3c`, `AA B C 1` as `/rest/nodes` writes it), the IoX name of a Device or of one of its Buttons, or the ID of a Home Assistant entity from the isy994 integration.

## Credentials

The tool reads its credentials from a gitignored folder, never from the repo (ADR 0004). By default that is the repo's `.private/`. A git worktree has no `.private/` of its own, so pass the main checkout's with `--private-dir`, or set `INSTEON_PRIVATE_DIR`.

| File | Holds |
| --- | --- |
| `eisy/iox.local.env` | `IOX_HOST`, `IOX_PORT` (8443), `IOX_PROTO` (https), `IOX_USER_NAME`, `IOX_PASSWORD`; optionally `IOX_CERT_SHA256`. For SSH (`programs`, and the IoX backup every command that changes IoX takes): `IOX_SSH_USER`, `IOX_SSH_PASSWORD`, optionally `IOX_SSH_PORT` (22) |
| `eisy/known_hosts` | Optional: the Controller's SSH host key. Without it, the tool checks `~/.ssh/known_hosts` |
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

### `programs`

This command is read-only. It exports every IoX program and variable as text, the input to migrating them by hand into the contexts that own them (ADR 0012). For each program it prints:

- its folder, id, enabled and run-at-startup state, whether its If was last true, and when it last ran;
- its If, Then and Else, with Devices, Scenes and variables by name;
- the Devices and Scenes it names, with their Home Assistant entity IDs. A Scene brings its members' Devices with it;
- the Home Assistant automations that reference any of the same entities, as possible duplicates. Either side may only watch a Device (in an If, or a trigger), so check each match by hand.

Each variable shows its type, id, name, value, init, and the programs that use it.

The list comes from `/rest/programs` and `/rest/vars`. The bodies come from the D2D files (`/var/isy/FILES/CONF/D2D/*.PGM`) over SSH. Only root can read them, so the tool runs one `tar` under `sudo -S` and sends the SSH password on stdin, never on a command line. Statements are decoded in the shapes IoX 6.0.5 writes. Any other shape is printed raw as `[not decoded]` and counted at the top, and a file that will not parse is printed whole as `[file not parsed]`, so no logic is dropped silently. The Devices such statements name still count toward possible duplicates.

What HA automations reference comes from Home Assistant's own `search/related`: the entities and devices an automation names. An automation that reaches a Device only through an area or label isn't matched.
### `audit`

The Link Audit. It is read-only, but it holds the lock throughout, since every Link-table read is a Modem operation. For each Device, it compares the Links the Device holds (`G_DEV_ALL`) with those the Controller expects (`G_ISY_ALL`). The Controller keeps no expected table for the Modem, so the Modem's expected Links are mirrored from the Devices' tables: a Device's Link with the Modem implies one in the Modem with the same group and the opposite role. It reports:

- **Unresponsive Devices**, with their Scenes. A Device that IoX gives up on (HTTP 501, after about 22 s) or that does not finish within `--timeout` (120 s by default) is an Unresponsive Device, not an error. Only the Administrator decides whether to Remove or Restore each one.
- **Stale Links**: Links a Device or the Modem holds to an address the Controller has no Device at, or to an Unresponsive Device found in the same run. Each comes with the Scenes it serves.
- **Differences**: Links that are `missing` (expected but not held), `extra` (held but not expected), or whose `data` differs, with their Scenes. A missing Link to an Unresponsive Device is marked as such.

Links are matched on role, group and linked address. Only flag bits 0x80 (in use) and 0x40 (controller) count, and records not in use are left out ([research/iox-api.md](./research/iox-api.md) §4). The Modem's Links are compared without their data, which the Controller does not model.

Battery Devices and disabled Devices are skipped unless named. For a named Battery Device, the tool asks the Administrator to hold its Set button first. Progress goes to stderr, the report to stdout. `--no-modem-links` skips the Modem's table, which takes about two records a second. When Devices are named, only they and the Modem's Links to them are compared.

### `refs`

Lists every Reference to a Device's entities and changes nothing. The Device's entities are its isy994 entities, plus helpers on the same HA device, such as `switch_as_x`. Its Scenes' switches are not included.

- **Home Assistant**: automations, scripts, scenes, groups (including group helpers) and persons, from HA's own `search/related`; and every Lovelace dashboard HA returns a config for, storage or YAML, down to the path in the config. Helpers that keep an entity in their config entry's options, such as template and min_max helpers, can't be searched, because the API returns those options only by starting an options flow. Template entities in HA's own YAML can't be searched either. The report names each such helper so it can be checked by hand.
- **Repo**: every gitignored `*.local.yaml` overlay whose token value is the entity ID or its bare object ID, read as YAML (ADR 0009), then each tracked file using `{TOKEN}`, and each tracked file that names the entity directly, such as a blueprint input or a test. Overlays live only in the main checkout, so from a worktree pass `--repo` with the main checkout's path. Overlay values are never printed.

The output is printed only. Never paste it into tracked files: it holds real entity IDs.

### `restore DEVICE|modem`

Restores a Device's Links (`RestoreDevice`) or the Modem's (`ReplaceModem`) from the Controller's database. The Modem's takes minutes for a full table. For a Battery Device, the tool asks the Administrator to hold its Set button first, and reports any Pending Writes IoX still holds afterwards. Run `audit` on it afterwards to confirm.

### `remove DEVICE [--clean]`

Removes a Device and cleans up after it. In order:

1. Takes each of its Buttons out of each Scene (`RemoveFromGroup`).
2. Writes the updates (`WriteDeviceUpdates`) to it and to each of its former Scene peers: the other Devices in those Scenes.
3. Removes it (`RemoveNode`).
4. Runs a Link Audit on the former Scene peers and Restores each that differs. Reads the Modem's Links and Restores the Modem if it still holds a Link to the removed Device. `--no-modem-links` skips that read.
5. Reconciles HA: reloads the isy994 integration, then removes the Stale HA Devices, as `prune` does.
6. With `--clean`, takes the Device's References out of live HA.

First, it reads the Device's Links, as `audit` does, even in a dry run. A Device that does not answer is an Unresponsive Device: step 2 writes nothing to it, but its peers and the Modem are still cleaned. For a Battery Device, the tool asks the Administrator to hold its Set button, or to type `u` if it is gone. Battery peers are skipped in step 4; audit them by name afterwards.

References are always listed: live HA's as the edits `--clean` would make or as hand edits, and the repo's, which are never edited. With `--clean`, a dry run prints each edit as a diff. `--clean --apply` saves them through HA's config API, never `.storage`, after the Reconcile:

- An entity is taken out of each list that names it. A trigger, condition, action, card or row left naming nothing is deleted, and so is a stack or card it leaves empty.
- An automation, script, scene or dashboard is never deleted. Nor is a branch whose condition goes while its actions stay, or anything named in a template. Those are left for a hand edit, and so are groups, persons, helpers, YAML-defined items and YAML dashboards.
- An automation's own conditions may be left empty, which makes it run more often. Read the diff.

### `add ADDRESS NAME`

Adds a Device by its Insteon Address (`AddNode`), erasing its Links. `--from-links` keeps them instead, and has IoX add the Devices they name and build Scenes from them. `--battery` prompts to hold the Set button first; a new Device that ignores the Modem may need that too. Then it Reconciles HA and prints the new entity IDs.

### `scene`

```sh
uv run insteon scene create NAME [--address N]
uv run insteon scene delete SCENE
uv run insteon scene add SCENE BUTTON (--controller | --responder)
uv run insteon scene remove SCENE BUTTON
uv run insteon scene level SCENE RESPONDER PERCENT [--ramp INDEX] [--controller BUTTON]
```

SCENE is a Scene address or IoX name. BUTTON is a Button address (`1A 2B 3C 3`), a Button's IoX name, or anything that names a Device, which means its primary Button. A Button controls at most one Scene, so `add --controller` refuses a Button that already controls another. After each change, the tool writes the updates to every Device whose Links it changed. `level` sets the on level IoX sends, as 0-255, for the Scene itself or for one Controller Button. Creating or deleting a Scene Reconciles HA, which adds or removes its switch.

## Rails

Every command that changes something follows these rails (`insteon/rails.py`):

- **Dry run** unless given `--apply`.
- **Lock**: `<private>/insteon.lock` (an `flock`). A second command fails at once and names the holder.
- **Backups** are taken under the lock, before any change. A backup that fails stops the command.
  - IoX: all of `/var/isy/FILES/CONF/` as one tar, read over SSH as root (`sudo -S`, the password on stdin), to `<private>/eisy/backups/<time>.tar`. About 10 MB, in a few seconds. Every command that changes IoX takes it.
  - Home Assistant registries: the device registry, entity registry and isy994 config entries, as JSON, in `<private>/insteon-backups/<time>-<command>/`. This is a record to re-create from by hand, not a Home Assistant backup.
  - Home Assistant config: `remove --clean` writes each automation, script, scene and dashboard config it is about to save, as read just before, to `ha-config.json` in the same folder. To undo an edit, POST that config back through HA's config API, or paste it into the editor. A full Home Assistant backup isn't taken: it is slow, and the edits it would cover are these.
- **One Modem operation at a time**: each write waits until IoX reports it is no longer busy.
- **Battery Devices**: the tool prompts the Administrator to hold the Set button before writing, one Device at a time. Then it waits for IoX to finish and reports any Pending Writes left.

## Code

| Module | Does |
| --- | --- |
| `insteon/address.py` | Insteon Addresses and Button addresses, in every format |
| `insteon/network.py` | Devices, Buttons, Scenes, Links and the Modem, with no wire formats |
| `insteon/iox.py` | All of IoX: REST, the SOAP envelope, the event stream, Link-table reads (ADR 0013), and the D2D files over SSH |
| `insteon/program.py` | IoX programs and variables, with no wire formats |
| `insteon/ha.py` | Home Assistant's WebSocket API |
| `insteon/resolve.py` | DEVICE argument → Device, and a Device or Scene → its HA entities |
| `insteon/rails.py` | Dry run, lock, backups and the Battery Device prompt |
| `insteon/config.py` | Finds the private folder and reads the credentials |
| `insteon/clean.py` | Taking a Device's References out of live HA's configs |
| `insteon/inventory.py`, `insteon/prune.py`, `insteon/programs.py`, `insteon/audit.py`, `insteon/refs.py`, `insteon/restore.py`, `insteon/remove.py`, `insteon/add.py`, `insteon/scene.py` | The commands |
| `insteon/cli.py` | The `insteon` entry point |

## Tests

```sh
uv run pytest insteon-network
```

The tests run the real clients against a fake IoX, a fake SSH login on the Controller, and a fake Home Assistant, all on localhost (`tests/insteon_fakes.py`). These serve made-up responses in IoX 6.0.5's shapes, with placeholder names, addresses and uuid. Real captured responses stay in `.private/`. The fake IoX records every write and changes its network the way IoX would, so the tests assert the exact calls, their order and their parameters.

The writes have been tested only against the fake. What they send follows the Controller's own schema; how IoX answers is still to confirm by hand ([research/iox-api.md](./research/iox-api.md) §9).

## After re-adding isy994

Home Assistant gives each isy994 device the area named after its IoX folder. Check those areas by hand, and fix any that don't match the room.
