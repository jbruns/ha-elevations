# The repo is public, so it holds no secrets or PII and blueprints never hard-code entities

This repo is published publicly so that Home Assistant can import blueprints by `source_url`, and anything committed stays in git history for good. So nothing in it may identify the household or give access to it. Blueprints therefore take every camera, phone, zone and notify target as an input selector and never hard-code an entity ID, even where there is only one camera. Examples and docs use placeholders. This is deliberate. Do not "simplify" a blueprint by inlining the real entities.
