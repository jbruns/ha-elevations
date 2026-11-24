# PyISY and Home Assistant's isy994 integration

Researched 2026-11-23. Read-only source review, plus one live change (switching the integration from HTTP to HTTPS) on a Controller running IoX 6.0.5.

Vocabulary follows [`../CONTEXT.md`](../CONTEXT.md). PyISY and HA say "node" for a Device or Button and "group" for a Scene.

**Confidence labels**
- **V**: verified on a live system on 2026-11-23.
- **H**: read in source code or a first-party document.
- **M**: indirect or community source.
- **L**: inference.

## Sources

| Tag | Source |
|---|---|
| `pyisy/` | PyISY tag v3.8.0 (commit a385680), https://github.com/automicus/PyISY. Core isy994 pins `pyisy==3.8.0` in `manifest.json`. |
| `isy994/` | `homeassistant/components/isy994` in https://github.com/home-assistant/core at commit 237ce4ed. |
| `entity_registry.py` | `homeassistant/helpers/entity_registry.py`, `async_get_or_create`, same commit. |

Citations are `pyisy/<file>:<line>` or `isy994/<file>:<line>`.

---

## 1. What PyISY calls

### Transport
- Every call is an HTTP GET to `<base>/rest/<path>?<query>` (`pyisy/connection.py:148-182`). **H**
- PyISY uses SOAP only to subscribe to the legacy TCP event stream (`pyisy/events/strings.py:7-49`, `pyisy/events/tcpsocket.py:213-224`). HA passes `use_websocket=True` (`isy994/__init__.py:117`), so it uses `/rest/subscribe` instead (`pyisy/events/websocket.py:114-115`). **H**
- **PyISY makes no SOAP calls for commands or configuration.** **H**

### Read endpoints
| Purpose | URL | Cite |
|---|---|---|
| ping | `/rest/ping` | `pyisy/connection.py:318-322` |
| config (uuid, firmware) | `/rest/config` | `pyisy/connection.py:330-333`; uuid at `pyisy/configuration.py:94` |
| programs | `/rest/programs[/<id>]?subfolders=true` | `pyisy/connection.py:335-341` |
| nodes, Scenes, folders | `/rest/nodes?members=false` | `pyisy/connection.py:343-346` |
| status | `/rest/status` | `pyisy/connection.py:348-351` |
| variables | `/rest/vars/definitions/{1,2}`, `/rest/vars/get/{1,2}` | `pyisy/connection.py:353-385` |
| network resources | `/rest/networking/resources` | `pyisy/connection.py:387-391` |
| node notes, status | `/rest/nodes/<addr>/notes`, `/rest/nodes/<addr>/get/ST` | `pyisy/nodes/nodebase.py:184-212`, `pyisy/nodes/node.py:446-447` |

Scene membership is read: `Group.members` and `controllers` come from the nodes XML (`pyisy/nodes/group.py:72-95`), and `Node.get_groups()` maps them back (`pyisy/nodes/node.py:499-515`). `?members=false` does not drop a Scene's `<members>`. **H**

### Write methods (v3.8.0)
- **Device and Scene** (`NodeBase`): `send_cmd` → `/rest/nodes/<addr>/cmd/<CMD>[/<val>[/<uom>]]` (`pyisy/nodes/nodebase.py:252-269`); on, off, fast on/off, brighten, dim, fade, beep (`:271-345`); `enable` / `disable` (`:283-301`); `rename` → `/rest/nodes/<addr>/change?name=` (firmware 5.2+, Devices and Scenes; `:347-370`). **H**
- **Device only**: `set_on_level` → `cmd/OL` and `set_ramp_rate` → `cmd/RR` (`pyisy/nodes/node.py:594-610`); lock, climate and fan commands (`:523-592`); Z-Wave parameters and lock codes (`:317-435`). **H**
- **Root**: `ISY.query` → `/rest/query[/<addr>]` (`pyisy/isy.py:275-292`); `send_x10_cmd` (`:294-308`). **H**
- **Programs**: run, runThen, runElse, stop, enable, disable (`pyisy/programs/folder.py:146-180`); run at startup on/off (`pyisy/programs/program.py:181-187`). **H**
- **Variables**: `set_value`, `set_init` → `/rest/vars/set|init/<t>/<id>/<v>` (`pyisy/variables/variable.py:197-236`). **H**
- **Network resources**: `NetworkCommand.run` (`pyisy/networking.py:191-199`). **H**

### Not in PyISY
Scene membership changes, per-responder levels, adding or removing a Device, linking, writing Pending Writes, Scene create or delete, Restore, Replace, link-table reads, program edits. **H** All of these need SOAP (see [`iox-api.md`](iox-api.md)).

PyISY does surface node-change events (`ND`, `NR`, `GD`, `GR`, `MV`, `RG`, `NN`, `GN`) on `isy.nodes.status_events` (`pyisy/nodes/__init__.py:304-329`; codes at `pyisy/constants.py:966-1008`). It does not update its in-memory tree on them ("FUTURE: Handle additional node change actions", `pyisy/nodes/__init__.py:328`). **H**

---

## 2. How isy994 maps the Controller into HA

### Identity
- Config entry `unique_id` is the Controller's uuid (`isy994/config_flow.py:199-202,243-246`), read from `/rest/config` `<id>` (`pyisy/configuration.py:94`). It is MAC-formatted. Written `<uuid>` below. **H**
- **Hub device**: `identifiers={("isy994", "<uuid>")}` (`isy994/__init__.py:200-214`). **H**
- **One HA device per Device**, i.e. per node with no parent (`isy994/helpers.py:346-348`):
  - `identifiers={("isy994", "<uuid>_AA BB CC 1")}` (`isy994/helpers.py:318-325`). **H**
  - `model` starts `AA BB CC: <nodeDefId> (<type>)` (`isy994/helpers.py:298-304`); `manufacturer` is `Insteon` (`:295`); `suggested_area` is the IoX folder (`:324`); `via_device` is the hub. **H**
  - The integration's own device dict is keyed by the bare address, not the identifier (`isy994/helpers.py:348`). **H** (matters in §4)
- **Service devices**: "Variables" (`<uuid>_variables`) and "Networking" (`<uuid>_network`) (`isy994/__init__.py:155-173,217-237`). **H**
- **Entity `unique_id`** is `<uuid>_<node address>` (`isy994/entity.py:49`, `isy994/models.py:59-63`). Suffixes add entities on the same base: `_OL`, `_RR`, `_ERR`, `_enabled`, `_BL` (`isy994/sensor.py:200`, `number.py:125`, `select.py:90`, `switch.py:75`); `_query`, `_beep` buttons (`isy994/button.py:50-69`); `_button` event entities (`isy994/event.py:42,126-128`). **H**
- **Buttons** (keypad buttons `AA BB CC 2`..`8`, sensor sub-nodes) each get their own entity, attached to the primary node's HA device via `devices.get(node.primary_node)` (e.g. `isy994/light.py:35`, `isy994/sensor.py:187`). **H**
- Nodes whose name or path contains `{IGNORE ME}` are skipped (`isy994/const.py:73`, `isy994/helpers.py:341-344`). **H**

### Scenes
- Each Scene becomes a `switch` entity with `unique_id` `<uuid>_<scene address>` (numeric) (`isy994/const.py:129`, `isy994/helpers.py:362-364`). HA `scene` entities are not used. **H**
- The switch attaches to the Controller Button's device if the Scene has exactly one Controller Button, otherwise to the hub (`isy994/switch.py:51-57`, `isy994/entity.py:46-47`). **H**

### Programs
- Only the `HA.<platform>` folder convention makes entities: a subfolder holding a `status` program (and an `actions` program, except for binary_sensor) under a top-level `HA.binary_sensor`, `HA.cover`, `HA.fan`, `HA.lock` or `HA.switch` folder (`isy994/const.py:77-81,101-107`, `isy994/helpers.py:409-444`). **H**
- `unique_id` is `<uuid>_<status program id>`; no device (`isy994/models.py:81-83`). **H**
- Other programs are reachable only through the `isy994.send_program_command` action (`isy994/services.py:66-75,159-186`). **H**

### Variables
- Every integer and state variable becomes two `number` entities, value and `_init`, on the Variables device, with `unique_id` `<uuid>_<type>.<id>[_init]` (`isy994/number.py:85-119`, `pyisy/variables/variable.py:83-85`). Enabled by default only if the name contains `HA.` (`isy994/const.py:70,78`). **H**

### Actions (services)
`send_raw_node_command`, `send_node_command`, `rename_node`, `send_program_command`, and Z-Wave parameter and lock-code actions (`isy994/services.py:24-34`). None change Scene membership, link, or remove Devices. **H**

---

## 3. Stale devices and entities

- **No live handling.** HA ignores `NR` / `GR` events; the entity stays until the config entry reloads (`pyisy/nodes/__init__.py:304-329`). **H**
- **On setup or reload, stale entities are removed.** `_async_cleanup_registry_entries` removes registry entries not in `IsyData.unique_ids` (`isy994/util.py:10-36`, called at `isy994/__init__.py:183-184`; IDs listed in `isy994/models.py:65-110`). **H**
- **Stale HA Devices are not removed.** Nothing calls `async_remove_device`, so the HA device is left with no entities. **H**
- **The removal check is broken.** `async_remove_config_entry_device` (`isy994/__init__.py:249-257`) means to allow deletion only of devices the Controller no longer has. It compares `("isy994", key)` for each `key` in `runtime_data.devices`. Those keys are bare addresses plus `variables` / `network` (`isy994/helpers.py:348`, `isy994/__init__.py:156,168`). The real identifiers are `<uuid>_<address>` (`isy994/helpers.py:319`). The sets never intersect, so the function always returns True: any isy994 device, live ones and the hub included, can be deleted. A live device deleted this way returns on the next reload. **H**
  - For the tool: `config/device_registry/remove_config_entry` over the HA WebSocket API removes Stale HA Devices without refusal, so the tool must check staleness itself.
- **Reconcile after a Device is removed in IoX:** **H** (composed from the above)
  1. Reload the isy994 config entry. Stale entities go.
  2. Find isy994 devices whose identifier address is not in `/rest/nodes` and that have no entities left.
  3. Remove each with `config/device_registry/remove_config_entry`.

### Re-adding the integration keeps entity settings
- HA's entity registry keeps deleted entities. When an entity is re-created with the same `(domain, platform, unique_id)`, it gets back its `entity_id`, name, area and disabled state (`entity_registry.py`, `async_get_or_create`). **H**
- An entity can be renamed (`config/entity_registry/update`, `new_entity_id`) to an ID that only a deleted entity held: `_entity_id_available` counts such an ID as free, but not one that is registered or in the state machine. A rename must keep the domain. A refused rename answers `invalid_info` (`entity_registry.py` `_async_update_entity`; `components/config/entity_registry.py`). **H**
  - When isy994's reload removes an entity (`util.py`, `entity_registry.async_remove`), HA also removes its restored, unavailable state (`entity_registry.py` `_async_setup_entity_restore`), so its ID is then free. **H**
  - A deleted entity whose unique ID returns gets back its ID only if that ID is still free, otherwise a new one. It also gets back its name, icon, area, labels, aliases, device class, options, and disabled and hidden state (`async_get_or_create`). **H**
  - Through the API, `disabled_by` and `hidden_by` take only `user` or null. **H**
- isy994 has no reconfigure flow, so changing the URL from `http://` to `https://` means deleting and re-adding the integration. Done on 2026-11-23: all 390 entities came back with their settings, and stale devices from an older controller were dropped. **V**

---

## 4. Finding HA entities for an Insteon Address

Normalize first. Turn `1A.2B.3C` into three bytes. IoX node addresses are space-separated hex plus a Button number (`AA BB CC 1`), and IoX drops leading zeros within a byte (`AA B C 1`) (see [`iox-api.md`](iox-api.md) §6). Compare bytes numerically; never string-match the dotted form. **V**

- **HA device (one)**: list `config/device_registry/list`, keep devices with an `isy994` identifier, strip the `<uuid>_` prefix, parse the rest as three bytes plus a Button number (`isy994/helpers.py:318-319`). Get `<uuid>` from the hub device, the config entry `unique_id`, or `/rest/config`. The `model` prefix is a weaker fallback (`isy994/helpers.py:298`). **H**
- **Entities (several)**: list `config/entity_registry/list`, keep `platform == "isy994"`, and match `unique_id` on `<uuid>_` plus the same three bytes with any Button number and any suffix. Union this with entities whose `device_id` is the device above. **H**
  - device_id alone misses some: a node without a `primary_node` mapping falls back to the hub (`isy994/entity.py:46-47`), and Scene switches attach to a device only when it is the sole Controller Button (`isy994/switch.py:51-57`). **H**
- **Scenes the Device is in**: read them from `/rest/nodes` (`pyisy/nodes/group.py:72-95`), then map each Scene address to the switch with `unique_id` `<uuid>_<scene address>`. **H**
- **Programs and variables** do not link to devices in HA. Cross-reference them by parsing program bodies on the Controller (`/var/isy/FILES/CONF/D2D/*.PGM`, see [`iox-api.md`](iox-api.md) §5). **L**

Example placeholders: HA entity `light.example`, HA device identifier `("isy994", "<uuid>_AA BB CC 1")`.

---

## 5. pyisyox, udi_iox, and why we stay on core isy994

- **pyisyox** (https://github.com/shbatm/pyisyox, 6.0.2 released 2026-11-17) supports IoX 6 only. It uses the JSON `/api` plus REST, never SOAP, and authenticates with a portal JWT by default. It has no Scene membership changes, no Device add or remove, and no link-table writes. **H**
- **udi_iox** (https://github.com/shbatm/hacs-udi-iox, HACS, domain `udi_iox`) wraps pyisyox and calls itself a "clean break" from isy994. **H**
- **Core isy994** stays on PyISY 3.8.0, bumped in core PR #186552 (2026-10-15). Code owners are bdraco and shbatm. **H**
- **Decision: stay on core isy994.** **L** (reasoning)
  - udi_iox would add no network-management features: pyisyox has no SOAP, so Scene, linking and Restore work still needs our own tool.
  - It depends on the portal account for `/api` (refused for the local IoX user, see [`iox-api.md`](iox-api.md) §1), which conflicts with ADR 0013's no-vendor-cloud rule.
  - A new domain means new `unique_id`s, so the entity-registry restore in §3 would not apply. Every entity ID and Reference would have to be migrated.
  - Core isy994 on local REST over 8443 keeps working after the Admin Console retirement, since UDI says all APIs continue to function ([`iox-api.md`](iox-api.md) §7).
- Revisit if core isy994 moves to pyisyox, or if UDI deprecates `/rest`.
