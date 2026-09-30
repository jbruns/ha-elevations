from pathlib import Path

import pytest
import yaml

from testing.displays import (
    DisplaySource,
    assert_custom_cards_listed,
    assert_entities_documented,
)


ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "dashboards" / "custom-cards.yaml"
WORK = ROOT / ".pytest_cache" / "display-harness-tests"


def test_display_seam_fails_on_an_unlisted_custom_card() -> None:
    rendered = {"views": [{"cards": [{"type": "custom:not-in-manifest"}]}]}

    with pytest.raises(AssertionError, match="not-in-manifest"):
        assert_custom_cards_listed(rendered, MANIFEST)


def test_display_seam_fails_on_an_undocumented_entity() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "display.yaml"
    source.write_text("views: [view.yaml]\nprerequisites:\n  entities: []\n")
    display = DisplaySource.load(source)
    rendered = {"views": [{"cards": [{"type": "button", "entity": "light.example"}]}]}

    with pytest.raises(AssertionError, match="light.example"):
        assert_entities_documented(rendered, display)


def test_custom_card_manifest_names_repositories_and_versions() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())["custom_cards"]

    assert manifest["advanced-camera-card"] == {
        "repository": "dermotduffy/advanced-camera-card",
        "installed_version": "v8.1.0",
    }
