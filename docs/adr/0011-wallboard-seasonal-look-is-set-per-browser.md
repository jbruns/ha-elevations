# The Wallboard's seasonal look is set per browser

The Wallboard's theme and background change with the seasons and holidays. A dashboard view can only pin one fixed theme and background, and Home Assistant's global theme would change every Display. So the Wallboard view pins no theme. A Wallboard-local automation sets one of a small set of Wallboard-local themes on the kiosk browser through browser_mod: four seasonal looks, with holiday looks overriding them for a date range. Each theme carries its own background image.

## Considered Options

- **Pin a theme in the view and change it by hand each month.** Rejected: it is the toil that let the original monthly look lapse.
- **Change Home Assistant's global theme.** Rejected: it would restyle Our Home and every phone.

## Consequences

- Opened on any browser other than the kiosk, the Wallboard shows that browser's default theme. Only the kiosk follows the seasons.
- The Wallboard depends on browser_mod and on the kiosk browser being registered with it.
- Background images live in Home Assistant's `www/wallboard/` folder and are never committed (ADR 0004). The repo holds the theme definitions with placeholder image paths.
