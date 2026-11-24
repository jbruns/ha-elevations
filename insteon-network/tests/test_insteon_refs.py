import subprocess
from pathlib import Path

import pytest
from insteon_fakes import FakeHa, PASSWORD, TOKEN, USER, UUID
from insteon_fakes import fake_ha, fake_iox, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon.ha import HaClient
from insteon.iox import IoxClient
from insteon.refs import RefsReport, refs, render


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty git repo to hold tracked files and gitignored overlays."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("*.local.yaml\n")
    return root


def track(repo: Path, path: str, text: str) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", path], check=True)


async def run_refs(iox_url: str, ha_url: str, repo: Path, name: str) -> RefsReport:
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return await refs(iox, ha, name, repo=repo)


async def test_refs_finds_the_devices_entities_but_not_its_scenes(iox_url: str, ha_url: str, repo: Path) -> None:
    report = await run_refs(iox_url, ha_url, repo, "11.22.33")

    assert [entity.entity_id for entity in report.entities] == ["event.example_keypad_a"]


async def test_refs_finds_entities_by_unique_id_when_they_have_no_ha_device(
    iox_url: str, ha_url: str, repo: Path
) -> None:
    report = await run_refs(iox_url, ha_url, repo, "AA.BB.CC")

    assert [entity.entity_id for entity in report.entities] == [
        "light.example_dimmer",
        "number.example_dimmer_on_level",
    ]
    assert [entity.entity_id for entity in (await run_refs(iox_url, ha_url, repo, "Example Relay")).entities] == [
        "light.example_relay"
    ]


async def test_refs_lists_automations_scripts_scenes_and_groups_that_name_an_entity(
    iox_url: str, ha_url: str, fake_ha: FakeHa, repo: Path
) -> None:
    fake_ha.related["light.example_dimmer"] = {
        "automation": ["automation.example_evening"],
        "script": ["script.example_bedtime"],
        "scene": ["scene.example_movie"],
        "group": ["light.example_downstairs"],
        "device": ["dimmer"],
        "area": ["example_room"],
    }

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    section = text[text.index("Home Assistant") :].split("\n\n")[0]
    assert section.splitlines()[1:6] == [
        "  light.example_dimmer",
        "    automation: automation.example_evening",
        "    script: script.example_bedtime",
        "    scene: scene.example_movie",
        "    group: light.example_downstairs",
    ]
    assert "example_room" not in section


async def test_refs_includes_a_switch_as_x_helper_on_the_same_ha_device_and_its_references(
    iox_url: str, ha_url: str, fake_ha: FakeHa, repo: Path
) -> None:
    fake_ha.devices.append(
        {"id": "relay-device", "identifiers": [["isy994", f"{UUID}_A B C 1"]], "config_entries": ["isy-entry"]}
    )
    fake_ha.entities.append(
        {"entity_id": "fan.example_relay_fan", "unique_id": "x1", "platform": "switch_as_x", "device_id": "relay-device"}
    )
    fake_ha.related["fan.example_relay_fan"] = {"automation": ["automation.example_fan"]}

    text = render(await run_refs(iox_url, ha_url, repo, "0A.0B.0C"))

    assert "  fan.example_relay_fan  (switch_as_x helper on the same HA device)" in text
    assert "  fan.example_relay_fan\n    automation: automation.example_fan" in text


async def test_refs_finds_entities_in_every_dashboard_and_names_the_unreadable_ones(
    iox_url: str, ha_url: str, fake_ha: FakeHa, repo: Path
) -> None:
    fake_ha.dashboards = [
        {"url_path": "example-home", "title": "Example Home", "mode": "storage"},
        {"url_path": "example-wall", "title": "Example Wall", "mode": "yaml"},
        {"url_path": "example-broken", "title": "Example Broken", "mode": "yaml"},
    ]
    fake_ha.dashboard_configs = {
        None: {"views": [{"cards": [{"type": "tile", "entity": "light.example_dimmer"}]}]},
        "example-home": {
            "views": [
                {
                    "title": "Lights",
                    "cards": [{"type": "entities", "entities": ["light.example_relay", "light.example_dimmer"]}],
                },
                {"cards": [{"type": "markdown", "content": "{{ states('light.example_dimmer') }}"}]},
            ]
        },
        "example-wall": {"views": [{"cards": [{"entity": "light.example_dimmer_2"}]}]},
    }

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    assert "    dashboard Overview (default): views[0].cards[0].entity" in text
    assert "    dashboard Example Home (example-home): views[0].cards[0].entities[1]" in text
    assert "    dashboard Example Home (example-home): views[1].cards[0].content" in text
    assert "example-wall" not in text.split("Dashboard not read")[0]
    assert "  Dashboard not read: Example Broken (example-broken): lovelace/config: config_not_found" in text


async def test_refs_says_template_entities_are_not_searched(iox_url: str, ha_url: str, repo: Path) -> None:
    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    assert "Not searched, since the HA API does not return their config" in text


async def test_refs_follows_an_overlay_token_to_the_tracked_files_that_use_it(
    iox_url: str, ha_url: str, repo: Path
) -> None:
    (repo / "dashboards").mkdir()
    (repo / "dashboards" / "home.local.yaml").write_text(
        "# example overlay\nEXAMPLE_DIMMER: light.example_dimmer\nEXAMPLE_OTHER: light.example_dimmer_2\n"
    )
    track(repo, "dashboards/home.local.example.yaml", "EXAMPLE_DIMMER: light.example\n")
    track(repo, "dashboards/views/lights.yaml", "cards:\n  - entity: {EXAMPLE_DIMMER}\n  - entity: {EXAMPLE_OTHER}\n")
    track(repo, "lighting/blueprints/example.yaml", "input:\n  light:\n    default: light.example_dimmer\n")
    (repo / "untracked.yaml").write_text("entity: light.example_dimmer\n")

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    section = text[text.index("Repo (") :]
    assert section.splitlines()[1:] == [
        "  light.example_dimmer is {EXAMPLE_DIMMER} in dashboards/home.local.yaml",
        "    dashboards/views/lights.yaml:2",
        "  light.example_dimmer named directly",
        "    lighting/blueprints/example.yaml:3",
    ]


async def test_refs_never_prints_overlay_values_other_than_the_entity(
    iox_url: str, ha_url: str, repo: Path
) -> None:
    (repo / "home.local.yaml").write_text("EXAMPLE_SECRET: example-secret-value\nEXAMPLE_DIMMER: light.example_dimmer\n")

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    assert "example-secret-value" not in text and "EXAMPLE_SECRET" not in text


async def test_an_auto_generated_default_dashboard_is_not_reported_as_unreadable(
    iox_url: str, ha_url: str, repo: Path
) -> None:
    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    assert "Dashboard not read" not in text


async def test_refs_names_the_helpers_whose_config_ha_does_not_return(
    iox_url: str, ha_url: str, fake_ha: FakeHa, repo: Path
) -> None:
    fake_ha.entries += [
        {"entry_id": "t1", "domain": "template", "title": "Example Template"},
        {"entry_id": "m1", "domain": "min_max", "title": "Example Average"},
        {"entry_id": "g1", "domain": "group", "title": "Example Group"},
    ]

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    assert "    min_max: Example Average" in text
    assert "    template: Example Template" in text
    assert "Example Group" not in text  # groups are searched


async def test_refs_reads_overlay_values_as_yaml_and_matches_bare_object_ids(
    iox_url: str, ha_url: str, repo: Path
) -> None:
    (repo / "home.local.yaml").write_text(
        'EXAMPLE_QUOTED: "light.example_dimmer"\nEXAMPLE_LIST: [light.example_relay, light.example_dimmer]\n'
        "EXAMPLE_OBJECT_ID: example_dimmer\nEXAMPLE_NOT_IT: example_dimmer_2\n"
    )

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    assert "light.example_dimmer is {EXAMPLE_QUOTED} in home.local.yaml" in text
    assert "light.example_dimmer is {EXAMPLE_LIST} in home.local.yaml" in text
    assert "light.example_dimmer is {EXAMPLE_OBJECT_ID} in home.local.yaml" in text
    assert "EXAMPLE_NOT_IT" not in text


async def test_a_token_naming_two_of_the_devices_entities_credits_its_lines_to_both(
    iox_url: str, ha_url: str, repo: Path
) -> None:
    (repo / "home.local.yaml").write_text("EXAMPLE_BOTH: [light.example_dimmer, number.example_dimmer_on_level]\n")
    track(repo, "views/lights.yaml", "entities: {EXAMPLE_BOTH}\n")

    text = render(await run_refs(iox_url, ha_url, repo, "AA.BB.CC"))

    section = text[text.index("Repo (") :]
    assert section.splitlines()[1:] == [
        "  light.example_dimmer is {EXAMPLE_BOTH} in home.local.yaml",
        "    views/lights.yaml:1",
        "  number.example_dimmer_on_level is {EXAMPLE_BOTH} in home.local.yaml",
        "    views/lights.yaml:1",
    ]
