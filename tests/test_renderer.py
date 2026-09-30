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


def test_renderer_does_not_treat_javascript_template_literals_as_tokens() -> None:
    assert render_text("label: ${FOO}", {}) == "label: ${FOO}"
    assert render_text("return `${FOO}`;", {}) == "return `${FOO}`;"


def test_renderer_assembles_a_display_from_one_source_file_per_view() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    view = WORK / "view.yaml"
    source = WORK / "display.yaml"
    overlay = WORK / "overlay.local.yaml"
    view.write_text("title: Front Door\npath: front-door\ncards:\n  - type: picture-entity\n    entity: {DISPLAY_CAMERA}\n")
    source.write_text("title: Test Display\nurl_path: test-display\nviews:\n  - view.yaml\n")
    overlay.write_text("DISPLAY_CAMERA: camera.example\n")

    rendered = render_display(source, overlay)

    assert rendered == {
        "title": "Test Display",
        "views": [
            {
                "title": "Front Door",
                "path": "front-door",
                "cards": [{"type": "picture-entity", "entity": "camera.example"}],
            }
        ]
    }
    assert yaml.safe_dump(rendered)


def test_renderer_carries_display_top_level_keys() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    view = WORK / "view.yaml"
    source = WORK / "display.yaml"
    view.write_text("title: Home\ncards: []\n")
    source.write_text("title: Wallboard\nurl_path: dashboard-wallboard\nviews:\n  - view.yaml\nprerequisites:\n  entities: []\nsupport_packages: []\n")

    assert render_display(source) == {"views": [{"title": "Home", "cards": []}], "title": "Wallboard"}


def test_renderer_assembles_sections_from_one_source_file_each() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    section = WORK / "section.yaml"
    view = WORK / "view.yaml"
    source = WORK / "display.yaml"
    overlay = WORK / "overlay.local.yaml"
    section.write_text("type: grid\ncards:\n  - type: entity\n    entity: {DISPLAY_ENTITY}\n")
    view.write_text("title: Home\ntype: sections\nsections:\n  - section.yaml\n")
    source.write_text("views:\n  - view.yaml\n")
    overlay.write_text("DISPLAY_ENTITY: sensor.example\n")

    assert render_display(source, overlay) == {
        "views": [
            {
                "title": "Home",
                "type": "sections",
                "sections": [
                    {"type": "grid", "cards": [{"type": "entity", "entity": "sensor.example"}]}
                ],
            }
        ]
    }
