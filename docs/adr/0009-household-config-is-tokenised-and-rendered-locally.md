# Household config is tokenised and rendered locally

Household config is not reusable behaviour. A Display, support package or Frigate config belongs to this home, but the repo is public, so the committed source uses `{CONTEXT_NAME}` tokens wherever a value could identify the household or grant access to it. The real values live in gitignored `*.local.yaml` overlays, and one shared renderer writes paste-ready output into gitignored build folders.

Role entities are used only where the role is a domain term. Safety owns **Immediate Hazards**. Climate owns **Exterior Doors**. A Display or automation may consume those roles instead of copying the sensor lists. Tests render household config with placeholder values, so the public source and the CI run stay safe.

Deploy remains manual. The renderer prepares output for a maintainer to paste or copy, but nothing in this repo pushes automatically to Home Assistant.

## Consequences

- The committed source is reviewable without exposing real entity IDs, URLs, hostnames or names.
- A missing token value fails the render instead of producing a half-filled asset.
- Frigate and Displays use the same token convention and renderer.
- Local overlays and rendered output must never be committed.
- Cutover instructions tell the maintainer what to paste; they do not automate the paste.
