# Insteon Network

Keeps the home's Insteon devices, their links and their scenes correct, and keeps Home Assistant consistent when devices come and go. The Controller's only job is the network; Home Assistant does every automation (ADR 0012).

## Language

### The network

**Controller**:
The eisy running IoX. It holds the Modem and the database of Devices, Scenes and Links.
_Avoid_: ISY, hub, IoX (when meaning the box)

**Modem**:
The Controller's Insteon radio and powerline interface, which has its own Link table.
_Avoid_: PLM, hub, dongle

**Device**:
One Insteon product at one Insteon Address, such as a dimmer, relay, keypad or door sensor. Devices are added, removed, replaced and restored whole.
_Avoid_: Node, module, unit

**Insteon Address**:
The three-byte identity printed on a Device, such as `1A.2B.3C`. It is the same in every format the tool accepts.
_Avoid_: Device ID, MAC, node address

**Button**:
One part of a Device that can join a Scene: a keypad button, or the load of a dimmer or relay. A Device has one or more Buttons.
_Avoid_: Node, sub-node, channel, group

**Battery Device**:
A Device that sleeps and accepts Link writes only while someone holds it in linking mode.
_Avoid_: Sensor (sensors may be powered), sleepy device

### Scenes and links

**Scene**:
A set of Buttons that act together without the Controller or Home Assistant: each Controller Button drives every Responder Button directly.
_Avoid_: Group, link group, HA scene

**Controller Button**:
A Button whose press drives a Scene. A Button controls at most one Scene.
_Avoid_: Master, source

**Responder Button**:
A Button that follows a Scene, at a level the Scene sets for it.
_Avoid_: Slave, target

**Link**:
One record in a Device's or the Modem's link table, saying who it controls or responds to.
_Avoid_: ALDB entry, link record

**Stale Link**:
A Link that points at a Device the Controller no longer has, or at a Device that does not answer. Stale Links make commands slow or get them missed.
_Avoid_: Orphan, ghost link, dead link

### Operations

**Link Audit**:
A read-only comparison of the Links each Device and the Modem actually hold against what the Controller expects, reporting differences and Stale Links.
_Avoid_: Compare, scan, health check

**Restore**:
Rewriting a Device's or the Modem's Links from the Controller's database.
_Avoid_: Repair, resync, rewrite

**Replace**:
Moving a Device's Scenes and Links onto a new Device at a different Insteon Address, then retiring the old one.
_Avoid_: Swap, migrate

**Unresponsive Device**:
A Device the Controller has but that does not answer, whether it is gone from the wall or installed and unreachable. Only the Administrator decides whether to Remove or Restore it.
_Avoid_: Offline, dead, missing

**Pending Write**:
A change the Controller has accepted but not yet written to a Device, usually because it is a Battery Device or did not answer.
_Avoid_: Queued update, dirty

### Home Assistant side

**Reconcile**:
Bringing Home Assistant's devices and entities in line with the Controller, which is the authority on the Insteon network. Home Assistant never changes the Controller's state.
_Avoid_: Sync, mirror, import

**Reference**:
A place in Home Assistant that names one of a Device's entities: an automation, script, group, helper, dashboard or the repo's household config.
_Avoid_: Usage, dependency, footprint

**Stale HA Device**:
A Home Assistant device from the Controller integration that matches no Device the Controller has.
_Avoid_: Ghost, orphan, zombie
