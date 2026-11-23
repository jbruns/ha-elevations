# The Insteon tool talks to IoX over local SOAP and REST, not the eisy-ui JSON API

The Insteon Network tool calls IoX's SOAP (`/services`) and REST (`/rest`) endpoints on the local HTTPS port with a local admin user. IoX 6 also has a newer JSON API behind eisy-ui, which is easier to call but may need a token from UDI's cloud account. Managing the home's network must not depend on a vendor cloud, so we accept the older, thinly documented SOAP interface and pin its parameter names against the eisy's own `services.wsdl`.
