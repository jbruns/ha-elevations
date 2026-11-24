import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from insteon_fakes import FakeHa, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, UUID, USER
from insteon_fakes import eisy_ssh, fake_ha, fake_iox, fake_ssh, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon.ha import HaClient
from insteon.iox import IoxClient, IoxError, IoxFiles
from insteon.rails import IoxBackup, LockHeld, Rails, lock
from insteon.replace import ReplaceResult, render, replace
from insteon.resolve import ResolveError

# The new Dimmer, already added to IoX (as `insteon add` leaves it) and to HA.
NEW_DIMMER = (
    '<node flag="128" nodeDefId="DimmerLampSwitch_ADV"><address>7A 7B 7C 1</address><name>Example New Dimmer</name>'
    "<type>1.32.66.0</type><enabled>true</enabled><pnode>7A 7B 7C 1</pnode></node>"
)
NEW_KEYPAD = (
    '<node flag="128" nodeDefId="KeypadDimmer_ADV"><address>7A 7B 7C 1</address><name>Example New Keypad</name>'
    "<type>1.65.69.0</type><enabled>true</enabled><pnode>7A 7B 7C 1</pnode></node>"
    '<node flag="0" nodeDefId="KeypadButton_ADV"><address>7A 7B 7C 3</address><name>Example New Keypad A</name>'
    "<type>1.65.69.0</type><enabled>true</enabled><pnode>7A 7B 7C 1</pnode></node>"
)
OLD_ENTITIES = {"light.example_dimmer", "number.example_dimmer_on_level"}
REPLACES = ("ReplaceDevice", {"node": "AA BB CC 1", "NewNode": "7A 7B 7C 1", "firmware": "1.32.66.0"})


def add_new_device(fake_iox: FakeIox, xml: str) -> None:
    fake_iox.nodes_xml = fake_iox.nodes_xml.replace("</nodes>", xml + "</nodes>")


def new_entity(entity_id: str, unique_suffix: str, **fields: Any) -> dict[str, Any]:
    return {
        "entity_id": entity_id, "unique_id": f"{UUID}_{unique_suffix}", "platform": "isy994", "device_id": "new-dimmer",
        "name": None, "original_name": "Example New Dimmer", "area_id": None, "disabled_by": None, "hidden_by": None,
        "icon": None, "labels": [], "aliases": [], **fields,
    }


@pytest.fixture
def replaceable(fake_iox: FakeIox, fake_ha: FakeHa) -> FakeHa:
    """The Dimmer is to be replaced by the new Dimmer, which HA already has entities for.

    The old entities carry the Administrator's settings; the reload drops them
    once IoX no longer has the old Dimmer.
    """
    add_new_device(fake_iox, NEW_DIMMER)
    for entity in fake_ha.entities:
        if entity["entity_id"] == "light.example_dimmer":
            entity.update(name="Example Hall Light", area_id="hall", icon="mdi:example", labels=["example"], aliases=["hall"])
        if entity["entity_id"] == "number.example_dimmer_on_level":
            entity.update(disabled_by="user", hidden_by="user", original_name="Example Dimmer On Level")
    dimmer = next(d for d in fake_ha.devices if d["id"] == "dimmer")
    dimmer.update(area_id="hall", name_by_user="Example Hall Dimmer", labels=["example"])
    fake_ha.devices.append(
        {"id": "new-dimmer", "identifiers": [["isy994", f"{UUID}_7A 7B 7C 1"]], "config_entries": ["isy-entry"],
         "name": "Example New Dimmer", "name_by_user": None, "area_id": None, "labels": []}
    )
    fake_ha.entities += [
        new_entity("light.example_new_dimmer", "7A 7B 7C 1"),
        new_entity("number.example_new_dimmer_on_level", "7A 7B 7C 1_OL", disabled_by="integration",
                   original_name="Example New Dimmer On Level"),
    ]
    fake_ha.reload_drops = set(OLD_ENTITIES)
    return fake_ha


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "example.yaml").write_text("entity: light.example_dimmer\n")
    subprocess.run(["git", "-C", str(repo), "add", "example.yaml"], check=True)
    return repo


async def run(
    iox_url: str, ha_url: str, ssh: SshEndpoint, tmp_path: Path, repo: Path, old: str = "AA.BB.CC", new: str = "7A.7B.7C",
    *, apply: bool, answers: list[str] | None = None,
) -> ReplaceResult:
    files = IoxFiles("127.0.0.1", SSH_USER, SSH_PASSWORD, port=ssh.port, known_hosts=ssh.known_hosts)
    rails = Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")
    replies = list(answers or [])
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return await replace(
            iox, ha, rails, IoxBackup(files, tmp_path / "eisy" / "backups"), old, new, repo=repo,
            prompt=lambda message: replies.pop(0) if replies else "",
        )


def entity(fake_ha: FakeHa, entity_id: str) -> dict[str, Any] | None:
    return next((e for e in fake_ha.entities if e["entity_id"] == entity_id), None)


async def test_a_dry_run_shows_the_replace_and_each_rename_and_writes_nothing(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=False)

    assert fake_iox.writes() == [] and replaceable.writes() == []
    assert not (tmp_path / "eisy" / "backups").exists()
    text = render(result)
    assert "Would replace AA.BB.CC Example Dimmer with 7A.7B.7C Example New Dimmer" in text
    assert "light.example_new_dimmer -> light.example_dimmer" in text
    assert "number.example_new_dimmer_on_level -> number.example_dimmer_on_level" in text
    assert "Dry run: nothing changed. Pass --apply to replace it." in text


@pytest.fixture
def referenced(replaceable: FakeHa) -> FakeHa:
    """An automation and a dashboard name the old entities by ID."""
    replaceable.related["light.example_dimmer"] = {"automation": ["automation.example_dimmer_level"]}
    replaceable.configs["automation.example_dimmer_level"] = {
        "id": "a1",
        "triggers": [{"trigger": "state", "entity_id": ["light.example_dimmer"]}],
        "actions": [{"action": "number.set_value", "target": {"entity_id": "number.example_dimmer_on_level"}}],
    }
    replaceable.dashboards = [{"url_path": "example-board", "title": "Example", "mode": "storage"}]
    replaceable.dashboard_configs["example-board"] = {
        "views": [{"cards": [{"type": "entities", "entities": ["light.example_dimmer", "number.example_dimmer_on_level"]}]}]
    }
    return replaceable


async def test_applying_runs_iox_replace_then_reloads_prunes_and_renames_in_that_order(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [REPLACES]
    writes = [(c["type"], c.get("service") or c.get("device_id") or c.get("entity_id")) for c in replaceable.writes()]
    assert writes[0] == ("call_service", "reload_config_entry")
    assert ("config/device_registry/remove_config_entry", "dimmer") in writes
    renames = [i for i, (kind, _) in enumerate(writes) if kind == "config/entity_registry/update"]
    assert len(renames) == 2 and min(renames) > writes.index(("config/device_registry/remove_config_entry", "dimmer"))
    assert writes[-1] == ("config/device_registry/update", "new-dimmer")
    assert result.stopped is None and not result.failures
    assert "Replaced AA.BB.CC Example Dimmer with 7A.7B.7C Example New Dimmer" in render(result)


async def test_a_replace_leaves_every_reference_working_with_no_edits(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, referenced: FakeHa, tmp_path: Path, repo: Path
) -> None:
    before = (repr(referenced.configs), repr(referenced.dashboard_configs))

    await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert (repr(referenced.configs), repr(referenced.dashboard_configs)) == before
    assert not [c for c in referenced.writes() if c["type"] in ("rest POST", "lovelace/config/save")]
    for entity_id, unique_id in [
        ("light.example_dimmer", f"{UUID}_7A 7B 7C 1"),
        ("number.example_dimmer_on_level", f"{UUID}_7A 7B 7C 1_OL"),
    ]:
        found = entity(referenced, entity_id)
        assert found is not None and found["unique_id"] == unique_id
    assert entity(referenced, "light.example_new_dimmer") is None


async def test_the_renamed_entities_keep_the_old_names_areas_and_states(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    light = entity(replaceable, "light.example_dimmer")
    assert light is not None
    assert (light["name"], light["area_id"], light["icon"], light["labels"], light["aliases"]) == (
        "Example Hall Light", "hall", "mdi:example", ["example"], ["hall"]
    )
    level = entity(replaceable, "number.example_dimmer_on_level")
    assert level is not None
    # Its name came from IoX; it reads as before even though the new Device's IoX name differs.
    assert (level["name"], level["disabled_by"], level["hidden_by"]) == ("Example Dimmer On Level", "user", "user")
    device = next(d for d in replaceable.devices if d["id"] == "new-dimmer")
    assert (device["area_id"], device["name_by_user"], device["labels"]) == ("hall", "Example Hall Dimmer", ["example"])


@pytest.fixture
def keypad(fake_iox: FakeIox, fake_ha: FakeHa) -> FakeHa:
    """The new Device is a keypad: its Buttons don't match the Dimmer's."""
    add_new_device(fake_iox, NEW_KEYPAD)
    fake_ha.entities += [
        new_entity("light.example_new_keypad", "7A 7B 7C 1"),
        new_entity("number.example_new_keypad_on_level", "7A 7B 7C 1_OL"),
        new_entity("event.example_new_keypad_a", "7A 7B 7C 3_button"),
    ]
    fake_ha.reload_drops = set(OLD_ENTITIES)
    return fake_ha


async def test_buttons_that_dont_match_stop_after_iox_replace_and_print_the_references(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, keypad: FakeHa, tmp_path: Path, repo: Path
) -> None:
    keypad.related["light.example_dimmer"] = {"automation": ["automation.example_dimmer_level"]}

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [(REPLACES[0], {**REPLACES[1], "firmware": "1.65.69.0"})]
    assert keypad.writes() == []
    text = render(result)
    assert "AA.BB.CC Example Dimmer has Buttons 1; 7A.7B.7C Example New Keypad has 1, 3" in text
    assert "References to AA.BB.CC Example Dimmer" in text
    assert "automation: automation.example_dimmer_level" in text
    assert "example.yaml:1" in text


async def test_an_old_entity_with_no_counterpart_stops_after_iox_replace(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    replaceable.entities = [e for e in replaceable.entities if e["entity_id"] != "number.example_new_dimmer_on_level"]

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [REPLACES] and replaceable.writes() == []
    assert "number.example_dimmer_on_level has no counterpart on 7A.7B.7C Example New Dimmer" in render(result)


async def test_ha_is_not_touched_when_iox_still_has_the_old_device_after_the_replace(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    fake_iox.replace_keeps_old = True

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [REPLACES] and replaceable.writes() == []
    assert "still has AA.BB.CC; HA was not changed" in render(result)


async def test_once_iox_no_longer_has_the_old_device_a_second_run_only_reconciles_ha(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    fake_iox.replace_keeps_old = True
    await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)
    fake_iox.replace_keeps_old = False
    fake_iox._change(*REPLACES)  # IoX finishes the Replace, as after a reboot

    dry = render(await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=False))
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert "IoX's Replace: already done" in dry
    assert fake_iox.writes() == [REPLACES]
    assert entity(replaceable, "light.example_dimmer")["unique_id"] == f"{UUID}_7A 7B 7C 1"
    assert result.stopped is None


async def test_nothing_is_renamed_when_an_old_id_is_still_taken_after_the_reload(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    replaceable.reload_drops = {"light.example_dimmer"}

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert not [c for c in replaceable.writes() if c["type"] == "config/entity_registry/update"]
    text = render(result)
    assert "number.example_dimmer_on_level is still taken after the reload" in text
    assert "References to AA.BB.CC Example Dimmer" in text



async def test_a_new_device_the_controller_does_not_have_is_refused_before_any_write(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    with pytest.raises(ResolveError, match=re.escape("add it first with `insteon add 7A.7B.7C NAME --apply`")):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [] and fake_ha.writes() == []


async def test_a_new_device_ha_has_no_entities_for_yet_is_refused_before_any_write(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    add_new_device(fake_iox, NEW_DIMMER)

    with pytest.raises(ResolveError, match="HA has no entities for 7A.7B.7C Example New Dimmer yet"):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [] and fake_ha.writes() == []


async def test_a_new_device_already_in_scenes_is_refused(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    fake_iox.nodes_xml = fake_iox.nodes_xml.replace('<link type="32">A B C 1</link>', '<link type="32">7A 7B 7C 1</link>')

    with pytest.raises(ResolveError, match="already in Scenes \\(Example Scene\\)"):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert fake_iox.writes() == []


async def test_nothing_is_written_when_the_iox_backup_fails_or_the_lock_is_held(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "restore"), pytest.raises(LockHeld):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)
    bad = SshEndpoint(eisy_ssh.port, tmp_path / "missing-known-hosts")
    bad.known_hosts.write_text("")
    with pytest.raises(IoxError):
        await run(iox_url, ha_url, bad, tmp_path, repo, apply=True)

    assert fake_iox.writes() == [] and replaceable.writes() == []


async def test_a_refused_replace_changes_nothing_in_ha(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    fake_iox.refuse.add("ReplaceDevice")

    with pytest.raises(IoxError, match="SOAP ReplaceDevice: HTTP 500"):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert replaceable.writes() == []


async def test_a_battery_device_skipped_at_the_prompt_is_not_replaced(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    fake_iox.nodes_xml = fake_iox.nodes_xml.replace("<type>1.32.66.0</type>", "<type>16.2.64.0</type>")

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True, answers=["s"])

    assert fake_iox.writes() == [] and replaceable.writes() == []
    assert "IoX's Replace was not run" in render(result)


async def test_references_to_the_old_ha_device_are_listed_for_a_hand_edit(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    replaceable.related["dimmer"] = {"automation": ["automation.example_dimmer_press"]}
    replaceable.configs["automation.example_dimmer_press"] = {
        "id": "a2", "triggers": [{"trigger": "device", "device_id": "dimmer", "domain": "isy994", "type": "turned_on"}],
        "actions": [],
    }

    text = render(await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=False))

    assert "These name the old HA device, which the new one does not inherit" in text
    assert "  automation automation.example_dimmer_press\n" in text


async def test_a_failed_rename_is_reported_and_the_rest_still_run(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, replaceable: FakeHa, tmp_path: Path, repo: Path
) -> None:
    replaceable.refuse_updates = {"light.example_new_dimmer"}

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert [r.entity_id for r in result.renamed] == ["number.example_dimmer_on_level"]
    assert "failed: light.example_new_dimmer -> light.example_dimmer: config/entity_registry/update" in render(result)
    assert ("config/device_registry/update", "new-dimmer") in [(c["type"], c.get("device_id")) for c in replaceable.writes()]
