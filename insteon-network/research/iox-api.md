# IoX API research: managing the Insteon network without the Admin Console

Researched 2026-11-23 against a Controller (eisy) on IoX 6.0.5. Scope: what a CLI needs to add, remove, Replace and Restore Devices, edit Scenes, run a Link Audit, and export programs for review.

Vocabulary follows [`../CONTEXT.md`](../CONTEXT.md). IoX calls a Device or Button a "node" and a Scene a "group", so API names use those words.

**Confidence labels**
- **V**: verified on a live Controller (IoX 6.0.5) on 2026-11-23.
- **H**: a first-party document or UDI source code says so.
- **M**: first-party but old or indirect, or consistent community sources.
- **L**: a single community post, or inference.

## Sources

| Tag | Source |
|---|---|
| [live-WSDL] | The Controller's own `/services.wsdl`, which imports `/WEB/udiws30.ws`, `udiobjs.xsd` and `udievnts.xsd` (IoX 6.0.5, © UDI 2007-2013). Fetched 2026-11-23. |
| [REST-wiki] | https://wiki.universal-devices.com/ISY_Developers:API:REST_Interface |
| [SOAP-wiki] | https://wiki.universal-devices.com/ISY_Developers:API:Soap |
| [WSSDK] | https://docs.universal-devices.com/developers/docs/3.1.1/ISY-WS-SDK-Manual.pdf (firmware 3.1.1, 2011) |
| [EISYUI-family] | https://github.com/NuCoreAI/nucore-ai/blob/main/design/iox_apis/family.ts (UDI first-party copy of eisy-ui's backend route for the Insteon family) |
| [NUCORE-wrapper] | https://github.com/NuCoreAI/nucore-ai/blob/main/src/iox/iox_wrapper.py, `src/iox/iox_definitions.py`, `src/iox/diagnostics/insteon_diag.py` (UDI first-party) |
| [NUCORE-auth] | https://github.com/NuCoreAI/nucore-ai/blob/main/design/iox_apis/authentication.md |
| [NUCORE-groups] | https://github.com/NuCoreAI/nucore-ai/blob/main/design/iox_apis/groups-concept.md |
| [NUCORE-events] | https://github.com/NuCoreAI/nucore-ai/blob/main/design/iox_apis/subscription_events.md |
| [ISYlib] | https://github.com/evilpete/ISYlib-python (community, ISY994 era) |
| [pyisyox-conn] | https://pyisyox.readthedocs.io/en/latest/_sources/connection-flow.md.txt |
| [eisyui-alpha] | https://forum.universal-devices.com/topic/45210-eisy-ui-alpha-release/ (UDI staff, 2025-06-05) |
| [eisyui-release] | https://forum.universal-devices.com/topic/45390-eisy-ui-is-released/ |
| [eisyui-status] | https://forum.universal-devices.com/topic/47042-eisy-ui-is-getting-there-heres-where-we-stand/ |
| [AC-eol] | https://www.universal-devices.com/?p=278228 |
| [iox6-ports] | https://forum.universal-devices.com/topic/45676-questions-after-update-to-iox-v600/page/3 (UDI staff Geddy and bmercier) |
| [eisy-guide] | https://wiki.universal-devices.com/Eisy:User_Guide |

---

## 1. Transport and auth

| Port | Serves | Auth | Conf |
|---|---|---|---|
| 8443 HTTPS | IoX direct: `/rest`, `/services`, `/rest/subscribe` | Local IoX user, HTTP Basic | **V** |
| 8080 HTTP | Same as 8443, in cleartext | Local IoX user, HTTP Basic | **V** |
| 443 / 80 | eisy-ui and its proxy, including the JSON `/api` | UD portal account email and password, or a JWT | **V** (443) |
| 3000 | PG3x | — | **H** [iox6-ports] |

- Both 8443 and 8080 answer with the local IoX user. **V**
- There is no UD-supported way to turn 8080 off. The UD wiki tells PG3 users to connect over HTTP. **V** (observed), **M** (no staff statement found on disabling it).
- The certificate on 8443 is self-signed. TLS 1.0 and 1.1 are refused [pyisyox-conn]. **M**
- The JSON `/api` on 443 rejects the local IoX user with `username: Invalid email address`. It wants the UD portal account's email and password. The Controller signs the resulting JWT locally. **V**
- [NUCORE-auth] describes `POST /api/login {username, password, local:true}` against locally cached credentials. It is untested here, and the login form above rejected a non-email username. **H** (source), unverified.
- Only the admin user slot works; "User 1, 2" have not worked since early IoX 5 — https://forum.universal-devices.com/topic/41556-need-clarification-on-passwords-for-ssh-access-to-eisy-and-assiging-users-in-admin-console **M**

---

## 2. REST (`/rest/...`)

Every call is an HTTP GET with Basic auth, except file uploads [WSSDK §6]. **H**

### Read
- `/rest/nodes`: Devices, Buttons, Scenes and folders. Each `<group>` lists members as `<link type="16|32">`, where 16 = Controller Button and 32 = Responder Button [REST-wiki]. **H**, **V** (fetched)
- `/rest/nodes/<addr>`, `/rest/nodes/<addr>/notes`, `/rest/status[/<addr>]` [REST-wiki]. **H**
- `/rest/config` (uuid, firmware, features). **V**
- `/rest/programs?subfolders=true`: metadata only (id, parent, name, status, enabled, run-at-startup, last run times). No If/Then/Else bodies [REST-wiki]. **H**, **V**
- `/rest/vars/definitions/<1|2>` and `/rest/vars/get/<1|2>`. Type 1 is integer, type 2 is state [REST-wiki]. **H**, **V**
- `/rest/networking/resources` lists resources. **Never** GET `/rest/networking/resources/<id>`: that runs the resource [REST-wiki]. **H**
- `/rest/profiles?include=nodedefs,editors,linkdefs`: node definitions [iox6-ports][NUCORE-wrapper]. **M**

### Write
- `/rest/nodes/<addr>/cmd/<CMD>[/p1..]` (DON, DOF, DFON, DFOF, BRT, DIM, QUERY ...) [REST-wiki]. **H**
- `/rest/nodes/<addr>/set/<prop>/<val>`, e.g. OL (on level), RR (ramp rate) [REST-wiki]. **H**
- `/rest/nodes/<addr>/enable|disable` [REST-wiki]. **H**
- `/rest/nodes/<addr>/write`, "writes all pending changes to the device". In [WSSDK §6.5] but missing from the current wiki. Untested on IoX. **M**
- `/rest/batch/on|off` and `/rest/batteryPoweredWrites/on|off` [REST-wiki][WSSDK §6.1]. **H**
- `/rest/programs/<id>/{run,runThen,runElse,stop,enable,disable,enableRunAtStartup,disableRunAtStartup}` [REST-wiki]. **H**
- `/rest/vars/set/<t>/<id>/<v>` and `/rest/vars/init/<t>/<id>/<v>` [REST-wiki]. **H**

### What REST cannot do
REST has no call to add or remove an Insteon Device, start or stop linking, create or delete a Scene, change Scene membership, Restore or Replace. The wiki says some operations are "only available via SOAP" [REST-wiki]. **H**

---

## 3. SOAP (`POST /services`)

### Envelope

```
POST /services HTTP/1.1
Authorization: Basic <base64 user:pass>
Content-Type: text/xml; charset="utf-8"
SOAPACTION: "urn:udi-com:service:X_Insteon_Lighting_Service:1#<Op>"

<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
  <u:<BodyElement> xmlns:u="urn:udi-com:service:X_Insteon_Lighting_Service:1">
    <param>...</param>
  </u:<BodyElement>>
</s:Body></s:Envelope>
```

- Verified on IoX 6.0.5 for `DeviceSpecific`, with `#DeviceSpecific` as the SOAPACTION suffix. The reply is a bare 200 `UDIDefaultResponse`. **V**
- UDI's 2026 Python client sends a bare `SOAPAction: <Op>` [NUCORE-wrapper]. **H**
- Errors come back as HTTP status codes. ISYlib treats 501 on `CancelNodesDiscovery` as "not in linking mode", and 501 on `RemoveNode` as "already deleted" [ISYlib]. **M**

### Operation names vs body elements

The binding is document/literal [live-WSDL]. So the XML element in the body is the request message's part element, which is not always the operation name:

| Bound operation [live-WSDL] | Body element on the wire |
|---|---|
| `StartNodesDiscovery` | `DiscoverNodes` |
| `StopNodesDiscovery` | `CancelNodesDiscovery` |
| `SetLinkingMode` | `SetDeviceLinkMode` |
| `RestoreDevice` | `RestoreDeviceFromNode` |
| `RestoreDevices` | `RestoreDevicesFromNodes` |
| `Query` | `QueryAll` |

Every other operation uses its own name as the body element. **V** (read from the live WSDL). There is no bound operation named `DiscoverNodes` or `CancelNodesDiscovery`. UDI's eisy-ui backend sends the element names [EISYUI-family]. **H**

`DeviceSpecific` and `GetSysConf` are not in the published schema at all. `DeviceSpecific` works anyway (§4). **V**

### Operations relevant to the Insteon network

Parameters are from [live-WSDL] with inherited fields expanded, in schema order. Flag values are from `udiobjs.xsd`.

| Purpose | Operation (body element) | Params | Conf |
|---|---|---|---|
| Add a Device by Insteon Address | `AddNode` | `id` (address), `name`, `type` (device type), `flag` (NodeOperationsFlag) | **V** schema, **H** behaviour |
| ↳ NodeOperationsFlag | | 1 = add and reset existing settings; 2 = unused; 3 = add, find linked Devices, create the Links; 4 = add, find linked Devices, create no Links | **V** |
| Start linking mode | `StartNodesDiscovery` (`DiscoverNodes`) | `ntype`?, `flag`? (byte), `family`?; all optional | **V** schema |
| Stop linking mode | `StopNodesDiscovery` (`CancelNodesDiscovery`) | `flag` (NodeOperationsFlag) | **V** schema |
| Set linking mode | `SetLinkingMode` (`SetDeviceLinkMode`) | `flag` (LinkModeFlag) | **V** schema; values for this use unknown |
| Remove a Device | `RemoveNode` | `id` | **V** schema |
| Rename Device / Scene / folder | `RenameNode` / `RenameGroup` / `RenameFolder` | `id`, `name` | **V** schema |
| Create a Scene | `AddGroup` | `id` (new Scene address), `name`, `flag` (NodeTypeFlag: 1 node, 4 scene, 8 X10) | **V** schema |
| Delete a Scene | `RemoveGroup` | `id` | **V** schema |
| Add a Button to a Scene | `MoveNode` | `node`, `group`, `flag` (LinkModeFlag: 16 Controller, 32 Responder) | **V** schema |
| Remove a Button from a Scene | `RemoveFromGroup` | `node`, `group` | **V** schema |
| Responder level and ramp | `SetSceneProfile` | `node`, `controller`, `control` (e.g. OL, RR), `action` (value) | **V** schema, **M** semantics |
| Read responder levels | `GetSceneProfiles` | `node`, `controller` | **V** schema |
| Move into a folder | `SetParent` | `node`, `nodeType`, `parent`?, `parentType`? (0 unset, 1 node, 2 scene, 3 folder) | **V** schema |
| Enable or disable a Device | `SetNodeEnabled` | `node`, `flag` (0/1) | **V** schema |
| Flush Pending Writes | `WriteDeviceUpdates` | `node` | **V** schema |
| Restore one Device | `RestoreDevice` (`RestoreDeviceFromNode`) | `node`, `flag` (0 = full; 1 = do not regenerate scenes, for testing) | **V** schema |
| Restore all Devices | `RestoreDevices` (`RestoreDevicesFromNodes`) | `flag` | **V** schema |
| Restore the Modem | `ReplaceModem` | none | **V** schema |
| Delete the Modem | `RemoveModem` | none | **V** schema |
| Replace a Device | `ReplaceDevice` | `node` (old), `NewNode`, `firmware` | **V** schema; eisy-ui sends `id`, `name` = new address, `command` = device type [EISYUI-family] (**H**) |
| Batch mode | `SetBatchMode` | `flag` (0/1) | **V** schema |
| Battery Device writes | `SetBatteryDeviceWriteMode` | `flag` (0/1) | **V** schema |
| Full node config | `GetNodesConfig` | none; same shape as `/rest/nodes` | **V** schema |
| Link tables | `DeviceSpecific` (unpublished) | `command`, `node`, `option`, `flag`=1, `CDATA`="0 -1" | **V** |
| Read a config file | `GetSysConf` (unpublished) | `name` (e.g. `/CONF/D2D/<id>.PGM`) [ISYlib] | **M**; untested on IoX |

Where eisy-ui's 2026 code sends different parameter names from the live schema (`ReplaceDevice` above; `AddNode` with `ntype` in place of `type`; `WriteDeviceUpdates` and `RestoreDeviceFromNode` with `id` in place of `node`) [EISYUI-family], the live schema has not yet been tested against eisy-ui's names. **M**

The Admin Console uses the same protocol: UDI says its Python action constants "are the same as the Java SDK's IoXSOAPActions constants" [NUCORE-wrapper `iox_definitions.py`]. **M** (indirect)

---

## 4. Event stream and link-table reads

### Event stream
- WebSocket `wss://eisy.example:8443/rest/subscribe`, subprotocol `ISYSUB`, Origin `com.universal-devices.websockets.isy`. **V**
- IoX does not answer WebSocket pings. Turn client pings off (e.g. `ping_interval=None` in Python `websockets`), or the client drops the connection. **V**
- `_3` node-changed events: `ND` added, `NR` removed, `MV` moved into a Scene, `RG` removed from a Scene, `CL` Link changed, `WH` Pending Writes, `WD` programming a Device [WSSDK §5.5][NUCORE-events]. **H**
- `_5` busy (1) / not busy (0), and `_7` progress messages. **V**

### Link tables (`DeviceSpecific`)
Body: `<command>G_ISY_ALL|G_DEV_ALL|G_PLM_ALL|STOP</command><node>AA BB CC 1</node><option/><flag>1</flag><CDATA>0 -1</CDATA>`.

- `G_ISY_ALL` returns the Links the Controller expects the Device to hold. It answers at once, from cache. **V**
- `G_DEV_ALL` reads the Links from the Device itself. One dimmer with 6 records took about 6 s. **V** Old Devices may take minutes, with 20-30 s between records [NUCORE-wrapper `insteon_diag.py`]. **H**
- `G_PLM_ALL` reads the Modem's table [EISYUI-family]. **H**, untested here.
- `STOP` cancels a read [EISYUI-family]. **H**
- Records arrive on the event stream as `<control>_2</control>`. **V** (UDI's diagnostic describes `_7` progress events [NUCORE-wrapper]; on IoX 6.0.5 the records came as `_2`.)
  - `action` 3 is an expected record (`G_ISY_ALL`). `action` 2 is a record read from the Device (`G_DEV_ALL`). **V**
  - `eventInfo` fields: `ix` index; `ad` memory address (starts at 4088 = 0x0FF8 and counts down by 8); `fl` flags; `gr` group; `id` the linked Insteon Address as an integer; `data` D1..D3 as an integer. **V**
  - A record with `fl` 0 ends the table. **V**
- A session is wrapped in `_5` busy 1 then 0, followed by a `_7` "[All] Writing 0 bytes to devices" message. That is the routine flush of Pending Writes; nothing was written. **V**
- **A Link Audit is a client-side diff.** IoX has no compare call. UDI's own diagnostic reads `G_DEV_ALL` and `G_ISY_ALL` and diffs them, skipping deleted (0x22 / 0x02) and end-of-table records [NUCORE-wrapper `insteon_diag.py`]. **H**
- Flags differ in unused bits. On the tested dimmer, responder records were 0xE2 expected and 0xEA on the Device (bit 0x08). The audit should compare only in-use (0x80), controller (0x40) and bit 0x20. **V** (observed), **L** (mask choice).
- The Modem serves one link operation at a time. Run reads and Restores one after another [NUCORE-wrapper `insteon_diag.py`]. **H**

---

## 5. SSH and file layout

- SSH as the FreeBSD user `admin` works, and `sudo` is available. This user is separate from the IoX web user. **V**
- IoX config lives under `/var/isy/FILES/CONF/`. **V**
  - `CONF/D2D/*.PGM`: program bodies. **V**
  - `CONF/*.REC`: one file per Device address, probably the Controller's link model. **V** (files exist), **L** (meaning).
  - `CONF/INTEGER.VAR`, `CONF/STATE.VAR`, `CONF/NET/<id>.RES` are inferred from paths that ISYlib and pyisyox request. **L**
- The service is `isy`. UDI support's credential reset is `sudo rm /var/isy/FILES/CONF/0.UCF; sudo service isy restart` — https://forum.universal-devices.com/topic/44956-password-access-to-java-console **M**
- Reading these files gives a complete offline audit and backup. **Never write them while `isy` runs.** IoX keeps an in-memory model, and editing files does not program Devices. **M**

### Exporting programs for review
1. List with `/rest/programs?subfolders=true`. **H**
2. Bodies: read `/var/isy/FILES/CONF/D2D/*.PGM` over SSH. **V** Alternatives: SOAP `GetSysConf` [ISYlib] (**M**, untested), or `GET /api/triggers` for a JSON AST [NUCORE-wrapper] (**H**, needs the portal account).
3. Bodies use raw IDs. Resolve them against `/rest/nodes` and the variable definitions.

---

## 6. Pitfalls

- **Pending Writes and batch mode.** With batch mode on, changes only update the Controller's files. Turning it off writes everything pending. `batteryPoweredWrites/off` holds writes to Battery Devices even with batch mode off [WSSDK §6.1]. **H**
- **Battery Devices** must be put in linking mode by hand before any write, one at a time. Two in linking mode at once link to each other. They leave linking mode after about 4 minutes — https://forum.universal-devices.com/topic/17990-question-about-battery-devices-after-restore-reload-from-backup/ , https://wiki.universal-devices.com/ISY-99i/ISY-26_INSTEON:File_Menu **M**. The tool should queue, prompt the Administrator to press Set, then write.
- **Adding by address.** `AddNode` works for most wired Devices without pressing Set — https://wiki.universal-devices.com/ISY-99i/ISY-26_INSTEON:Adding_a_Device **M**. A new I2CS Device ignores a Modem that is not in its link table, so it may need the Set-button sequence — https://forum.universal-devices.com/topic/12454-plm-link-table-after-deleting-a-device **M**
- **Linking with Set** is `StartNodesDiscovery`, a Set press per Device, then `StopNodesDiscovery flag=1|3|4`. The flags match the Admin Console's three link-management options — https://wiki.universal-devices.com/ISY-99i/ISY-26_INSTEON:Link_Management_Menu **H** (menu), **M** (flag mapping).
- **Modem capacity.** Status reports stop after about 900 Links on a 2413S. Aim for at least twice as many Modem Links as Devices — https://wiki.universal-devices.com/INSTEON_No_Status_Feedback_From_Devices **H**. Restore Modem (`ReplaceModem`) compacts the table (forum). **M**
- **Removing a Device leaves Stale Links.** IoX asks the Modem to delete the Device's record, which the Modem marks inactive — https://forum.universal-devices.com/topic/12454-plm-link-table-after-deleting-a-device **M**. Links in *other* Devices that point at the removed one are often left behind — https://forum.universal-devices.com/topic/21955-how-to-remove-or-disable-links-to-nonexistent-device-in-isy-tables , https://forum.universal-devices.com/topic/9693-remaining-links-after-remove-device **M**
  - Suggested removal flow: read the Device's Scene peers from `/rest/nodes`; `RemoveFromGroup` from each Scene; `WriteDeviceUpdates` on the peers; `RemoveNode`; then Link Audit each peer and Restore any that differ. **L** (composed from the above)
- Records flagged 0x22 or 0x02 are logically deleted. Do not hand-edit them (forum moderator). **M**
- A Button controls at most one Scene, but can respond in many [NUCORE-groups]. **H**
- `/rest/nodes` drops leading zeros within address bytes (e.g. `AA B C 1`). Parse each byte as hex. **V**

---

## 7. The API's future

- UDI ends Admin Console support on 2026-12-15 [AC-eol]. **H**
- UDI says "All APIs ... continue to function" [eisyui-status]. **H**
- eisy-ui is the replacement UI. It was in alpha in June 2025 with "no supports for scenes or programs yet" [eisyui-alpha], then released [eisyui-release]. **H**
- eisy-ui's JSON `/api` is a translation layer over SOAP: its backend route for the Insteon family calls the same `/services` operations (e.g. `start-linking` → `DiscoverNodes`, `node/<addr>/restore` → `RestoreDeviceFromNode`, `links/device` → `DeviceSpecific G_DEV_ALL`) [EISYUI-family]. **H** So SOAP is what eisy-ui itself depends on, which makes it unlikely to disappear first. **L** (inference)
- Calling `/api` directly needs the UD portal account email and password. The local IoX user is refused. **V** That is why ADR 0013 chooses local SOAP and REST on 8443 ([`../../docs/adr/0013-insteon-tool-uses-local-soap-not-cloud-api.md`](../../docs/adr/0013-insteon-tool-uses-local-soap-not-cloud-api.md)).

---

## 8. Hardening notes

- Use 8443 only. 8080 carries the IoX password in cleartext and cannot be turned off in a UD-supported way. **V** Keep the Controller on a network segment that only Home Assistant and the Administrator's machine can reach. **L** (recommendation)
- Pin the Controller's self-signed certificate in the client, not "verify off". **L** (recommendation)
- Change the SSH password. Pressing the Controller's button three times (light blue) resets both SSH and IoX logins to `admin`/`admin`; the wiki says "Be sure to change the passwords" [eisy-guide]. **H** Changing it with `passwd` is community-documented, not staff-endorsed (thread in §1). **M**
- Store the IoX and SSH credentials outside the repo (gitignored env file), per ADR 0004.
- Read-only operations first: Link Audit and program export change nothing. Gate every write behind an explicit confirm.

---

## 9. Open questions

1. Do the eisy-ui parameter names (`ntype`, `id` in place of `node`, the `ReplaceDevice` fields) also work, or only the live schema's names? Test one harmless call each way.
2. Which SOAPACTION suffix does IoX expect for renamed operations: the operation name (`StartNodesDiscovery`) or the body element (`DiscoverNodes`)? Or does it dispatch on the body only?
3. Does `GetSysConf` still exist on IoX 6? (SSH already covers the need.)
4. Does `/rest/nodes/<addr>/write` still exist, or only `WriteDeviceUpdates`?
5. The values `SetDeviceLinkMode` accepts for "auto / I1 / I2CS" linking.
6. The exact flag mask for the Link Audit. Check it against `insteon_diag.py`.
7. Does `RemoveNode` alone rewrite the peers' link tables? Test on one Device, then Link Audit.
8. The format of `CONF/*.REC`, and whether it matches `G_ISY_ALL`.
