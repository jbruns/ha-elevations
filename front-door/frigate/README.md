# Frigate config

`config.yaml` is the Frigate config as a template. Anything secret or identifying is a `{FRIGATE_*}` placeholder.

The Frigate 0.18 add-on cannot load these values from anywhere else: `secrets.yaml` support is not released yet, and the `go2rtc` streams cannot use substitution on the add-on at all. So the deployed file has to contain the real values. It is rendered locally and never committed.

## Render

1. Create `front-door/frigate/secrets.local.yaml` (gitignored), with one `FRIGATE_NAME: value` line for every placeholder in `config.yaml`. Values are taken literally.
2. Run `scripts/render-frigate-config.py`. It writes `front-door/frigate/build/config.yaml` (gitignored) and fails on any undefined placeholder.
3. Deploy the rendered file as the add-on's `config.yaml`, then restart Frigate.

## Change the config

Edit `config.yaml`, never only the live file, so the two don't drift. If you change settings in the Frigate UI, copy the change back into the template.

Zone and mask `coordinates` are fractions of the frame (0 to 1), not pixels. Pixel coordinates move whenever the detect resolution changes, and before it was pinned to the detect stream's 896×672, Frigate picked a different one on some restarts. `front-door/tests/test_frigate_zones.py` checks the zones against where people and cars have actually been.
