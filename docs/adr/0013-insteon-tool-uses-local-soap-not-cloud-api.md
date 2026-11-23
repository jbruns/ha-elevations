# The Insteon tool talks to IoX over local SOAP and REST, not the eisy-ui JSON API

The Insteon Network tool calls IoX's SOAP (`/services`) and REST (`/rest`) endpoints over HTTPS on port 8443 with a local IoX user. IoX 6 also serves a JSON API through eisy-ui on port 443. It is easier to call, but it only translates to the same SOAP calls (eisy-ui's own backend makes them), and it signs in with the UD portal account rather than a local user. Managing the home's network must not depend on a vendor account, so we use SOAP and pin its names against the eisy's own `services.wsdl`.

## Consequences

- The Admin Console's end of support (2026-12-15) does not affect the tool: eisy-ui itself depends on the SOAP API.
- All IoX calls sit behind one client module, so a JSON adapter could replace SOAP if UD ever retires it. That would need portal credentials, and this ADR would be superseded.
- Some operations the tool needs, such as reading Link tables (`DeviceSpecific`), work on the device but are missing from the published WSDL. Tests must cover them against captured responses.
