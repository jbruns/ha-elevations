from pathlib import Path

import pytest
import yaml

from scripts.render_assets import UndefinedTokenError, render_display, render_text

WORK = Path(".pytest_cache") / "renderer-tests"


def test_renderer_fills_every_token() -> None:
    assert render_text("camera: {DASHBOARD_CAMERA}", {"DASHBOARD_CAMERA": "camera.example"}) == "camera: camera.example"


def test_renderer_fails_on_an_undefined_token() -> None:
    with pytest.raises(UndefinedTokenError, match="DASHBOARD_CAMERA"):
        render_text("camera: {DASHBOARD_CAMERA}", {})


def test_renderer_assembles_a_display_from_one_source_file_per_view() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    view = WORK / "view.yaml"
    source = WORK / "display.yaml"
    overlay = WORK / "overlay.local.yaml"
    view.write_text("title: Front Door\npath: front-door\ncards:\n  - type: picture-entity\n    entity: {DISPLAY_CAMERA}\n")
    source.write_text("views:\n  - view.yaml\n")
    overlay.write_text("DISPLAY_CAMERA: camera.example\n")

    rendered = render_display(source, overlay)

    assert rendered == {
        "views": [
            {
                "title": "Front Door",
                "path": "front-door",
                "cards": [{"type": "picture-entity", "entity": "camera.example"}],
            }
        ]
    }
    assert yaml.safe_dump(rendered)
