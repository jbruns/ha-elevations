# Insteon Network research

Researched 2026-11-23. Firmware tested: IoX 6.0.5 on an eisy (the Controller).

| File | Covers |
|---|---|
| [`iox-api.md`](iox-api.md) | IoX ports and auth; REST; SOAP operations with live-WSDL names and parameters; the event stream and link-table reads; SSH and file layout; pitfalls; the API's future after the Admin Console; hardening; open questions. |
| [`pyisy-and-ha-isy994.md`](pyisy-and-ha-isy994.md) | What PyISY calls and writes; how HA's isy994 maps Devices, Buttons, Scenes, programs and variables; stale-device behaviour and the broken removal check; finding HA entities for an Insteon Address; pyisyox / udi_iox and why we stay on core isy994. |

Each claim carries a source and a confidence label (V verified live, H first-party, M indirect, L inference).

## Feasibility verdict

A local CLI can do the whole job without the Admin Console. REST on port 8443 with the local IoX user covers reads, Device commands, programs and variables. SOAP on the same port covers everything REST lacks: adding by Insteon Address, linking, removal, Scene membership and responder levels, Pending Writes, Restore and Replace. The link-table read a Link Audit needs (`DeviceSpecific G_ISY_ALL` / `G_DEV_ALL` over the event stream) is verified on IoX 6.0.5. Program bodies can be read over SSH. eisy-ui's JSON API is a layer over the same SOAP and needs the UD portal account, so it adds nothing we need. Two things still need a person: pressing Set on Battery Devices and on some new I2CS Devices. Write calls other than the link-table read are checked against the live schema but not yet run, so the first write of each kind should be tested on one Device and followed by a Link Audit. On the Home Assistant side, core isy994 stays; Reconcile is a config-entry reload plus deleting Stale HA Devices, which the tool must check itself because isy994's own check is broken.
