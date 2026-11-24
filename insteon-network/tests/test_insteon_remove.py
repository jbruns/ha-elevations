import json
import subprocess
from pathlib import Path

import pytest
from insteon_fakes import FakeHa, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, USER, Rec, plm_record
from insteon_fakes import eisy_ssh, fake_ha, fake_iox, fake_ssh, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon.ha import HaClient
from insteon.iox import IoxClient, IoxError, IoxFiles
from insteon.rails import IoxBackup, LockHeld, Rails, lock
from insteon.remove import RemoveResult, remove, render

MODEM = 0x2A2B2C
DIMMER, KEYPAD, RELAY = 0xAABBCC, 0x112233, 0x0A0B0C
DIMMER_ENTITIES = {"light.example_dimmer", "number.example_dimmer_on_level"}
# The Dimmer responds in Example Scene (12345) and controls Example Three-Way (23456).
LEAVES_SCENES = [
    ("RemoveFromGroup", {"node": "AA BB CC 1", "group": "12345"}),
    ("RemoveFromGroup", {"node": "AA BB CC 1", "group": "23456"}),
]
WRITES_PEERS = [
    ("WriteDeviceUpdates", {"node": "A B C 1"}),
    ("WriteDeviceUpdates", {"node": "11 22 33 1"}),
]
REMOVES = [("RemoveNode", {"id": "AA BB CC 1"})]


@pytest.fixture
def network(fake_iox: FakeIox, fake_ha: FakeHa) -> FakeIox:
    """The Dimmer answers; the Keypad still holds Links to it, the Relay does not; so does the Modem."""
    fake_iox.actual["AA BB CC 1"] = [Rec(0xA2, 0, MODEM)]
    keypad = [Rec(0xA2, 0, MODEM), Rec(0xE2, 3, RELAY)]
    fake_iox.expected["11 22 33 1"] = list(keypad)
    fake_iox.actual["11 22 33 1"] = [*keypad, Rec(0xE2, 3, DIMMER)]
    relay = [Rec(0xA2, 0, MODEM), Rec(0xA2, 3, KEYPAD)]
    fake_iox.expected["A B C 1"] = list(relay)
    fake_iox.actual["A B C 1"] = list(relay)
    fake_iox.plm_records = [plm_record(0, 0xE2, 0, DIMMER), plm_record(1, 0xE2, 0, RELAY)]
    fake_ha.reload_drops = set(DIMMER_ENTITIES)
    return fake_iox


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "example.yaml").write_text("entity: light.example_dimmer\n")
    subprocess.run(["git", "-C", str(repo), "add", "example.yaml"], check=True)
    return repo


async def run(
    iox_url: str,
    ha_url: str,
    ssh: SshEndpoint,
    tmp_path: Path,
    repo: Path,
    name: str = "AA.BB.CC",
    *,
    apply: bool,
    answers: list[str] | None = None,
    ssh_password: str = SSH_PASSWORD,
    **options,
) -> RemoveResult:
    files = IoxFiles("127.0.0.1", SSH_USER, ssh_password, port=ssh.port, known_hosts=ssh.known_hosts)
    rails = Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")
    replies = list(answers or [])
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return await remove(
            iox,
            ha,
            rails,
            IoxBackup(files, tmp_path / "eisy" / "backups"),
            name,
            repo=repo,
            prompt=lambda message: replies.pop(0) if replies else "",
            **options,
        )


async def test_a_dry_run_shows_the_steps_and_writes_nothing_to_iox_or_ha(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=False)

    assert network.writes() == [] and fake_ha.writes() == []
    assert not (tmp_path / "eisy" / "backups").exists()
    text = render(result)
    assert "Would remove AA.BB.CC Example Dimmer" in text
    assert "AA.BB.CC 1  Responder in Example Scene (12345)" in text
    assert "AA.BB.CC 1  Controller in Example Three-Way (23456)" in text
    assert "2. Write the updates to AA.BB.CC Example Dimmer, 0A.0B.0C Example Relay, 11.22.33 Example Keypad" in text
    assert "Dry run: nothing changed. Pass --apply to remove it." in text


async def test_applying_leaves_scenes_writes_removes_then_restores_the_peer_and_modem_that_differ(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert network.writes() == [
        *LEAVES_SCENES,
        ("WriteDeviceUpdates", {"node": "AA BB CC 1"}),
        *WRITES_PEERS,
        *REMOVES,
        ("RestoreDeviceFromNode", {"node": "11 22 33 1", "flag": "0"}),
        ("ReplaceModem", {}),
    ]
    assert [w.device.name for w in result.restored.writes] == ["Example Keypad"]
    assert result.modem_links_left == 1


async def test_applying_reconciles_ha_after_the_iox_writes_reload_first_then_prune(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    writes = fake_ha.writes()
    assert writes[0]["type"] == "call_service" and writes[0]["service"] == "reload_config_entry"
    assert writes[0]["service_data"] == {"entry_id": "isy-entry"}
    assert "dimmer" in fake_ha.removed()
    assert not DIMMER_ENTITIES & {entity["entity_id"] for entity in fake_ha.entities}


async def test_an_unresponsive_device_is_not_written_to_but_its_peers_and_the_modem_are_cleaned(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, tmp_path: Path, repo: Path
) -> None:
    del network.actual["AA BB CC 1"]

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert network.writes() == [
        *LEAVES_SCENES,
        *WRITES_PEERS,
        *REMOVES,
        ("RestoreDeviceFromNode", {"node": "11 22 33 1", "flag": "0"}),
        ("ReplaceModem", {}),
    ]
    assert "Unresponsive" in render(result)


async def test_a_battery_device_the_administrator_says_is_gone_is_removed_as_unresponsive(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, "Example Door Sensor", apply=True, answers=["u"])

    assert network.writes() == [("RemoveNode", {"id": "5A 5B 5C 1"})]
    assert "the Administrator says it is gone" in render(result)


async def test_the_modem_is_not_restored_when_it_holds_no_link_to_the_device(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, tmp_path: Path, repo: Path
) -> None:
    network.plm_records = [plm_record(0, 0xE2, 0, RELAY), plm_record(1, 0x22, 0, DIMMER)]

    await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert ("ReplaceModem", {}) not in network.writes()


async def test_nothing_is_written_to_iox_or_ha_when_the_iox_backup_fails(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    with pytest.raises(IoxError):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True, ssh_password="wrong")

    assert network.writes() == [] and fake_ha.writes() == []


async def test_nothing_is_written_while_another_command_holds_the_lock(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "restore"), pytest.raises(LockHeld):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert network.writes() == [] and fake_ha.writes() == []


async def test_a_refused_write_stops_the_removal_before_the_device_is_removed(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    network.refuse.add("RemoveFromGroup")

    with pytest.raises(IoxError):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert [element for element, _ in network.writes()] == ["RemoveFromGroup"]
    assert fake_ha.writes() == []


async def test_references_in_live_ha_and_the_repo_are_listed_without_clean(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    fake_ha.related["light.example_dimmer"] = {"automation": ["automation.example_dimmer_level"], "group": ["group.example"]}
    fake_ha.configs["automation.example_dimmer_level"] = {
        "id": "a1",
        "triggers": [{"trigger": "state", "entity_id": ["light.example_dimmer", "light.example_relay"]}],
        "actions": [],
    }

    text = render(await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=False))

    assert "automation automation.example_dimmer_level: --clean would edit it" in text
    assert "group group.example: a group, which HA's config API cannot edit; edit it by hand" in text
    assert "example.yaml:1  light.example_dimmer" in text


@pytest.fixture
def referenced(fake_ha: FakeHa) -> FakeHa:
    fake_ha.related["light.example_dimmer"] = {"automation": ["automation.example_dimmer_level"]}
    fake_ha.related["dimmer"] = {"automation": ["automation.example_dimmer_press"]}
    fake_ha.configs["automation.example_dimmer_level"] = {
        "id": "a1",
        "triggers": [{"trigger": "state", "entity_id": ["light.example_dimmer", "light.example_relay"]}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }
    fake_ha.configs["automation.example_dimmer_press"] = {
        "id": "a2",
        "triggers": [{"trigger": "device", "device_id": "dimmer", "domain": "isy994", "type": "turned_on"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.example_bulb"}}],
    }
    fake_ha.dashboards = [{"url_path": "example-board", "title": "Example", "mode": "storage"}]
    fake_ha.dashboard_configs["example-board"] = {
        "views": [{"cards": [{"type": "entities", "entities": ["light.example_dimmer", "light.example_relay"]}]}]
    }
    return fake_ha


async def test_clean_shows_each_edit_as_a_diff_in_a_dry_run_and_edits_nothing(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, referenced: FakeHa, tmp_path: Path, repo: Path
) -> None:
    text = render(await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=False, clean=True))

    assert "-    - light.example_dimmer" in text
    assert "automation automation.example_dimmer_press: removing it would leave triggers empty" in text
    assert "dashboard Example (example-board)" in text
    assert referenced.writes() == []


async def test_clean_edits_live_ha_after_the_reconcile_and_backs_up_each_config_first(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, referenced: FakeHa, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True, clean=True, answers=["yes"])

    writes = referenced.writes()
    last_prune = max(i for i, c in enumerate(writes) if c["type"] == "config/device_registry/remove_config_entry")
    edits = [c for c in writes if c["type"] in ("rest POST", "lovelace/config/save")]
    assert writes.index(edits[0]) > last_prune
    assert [c.get("path") or c.get("url_path") for c in edits] == ["config/automation/config/a1", "example-board"]
    assert referenced.configs["automation.example_dimmer_level"]["triggers"][0]["entity_id"] == ["light.example_relay"]
    assert referenced.configs["automation.example_dimmer_press"]["triggers"][0]["device_id"] == "dimmer"
    assert referenced.dashboard_configs["example-board"]["views"][0]["cards"][0]["entities"] == ["light.example_relay"]
    saved = json.loads(result.cleaned.backup.read_text())
    assert [entry["key"] for entry in saved] == ["automation.example_dimmer_level", "example-board"]
    assert "light.example_dimmer" in json.dumps(saved)


async def test_clean_edits_nothing_in_live_ha_unless_the_administrator_confirms_the_diffs(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, referenced: FakeHa, tmp_path: Path, repo: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True, clean=True, answers=["n"])

    assert not [c for c in referenced.writes() if c["type"] in ("rest POST", "lovelace/config/save")]
    assert "Not confirmed: live HA was not edited" in render(result)


async def test_a_failed_peer_restore_after_the_removal_is_reported_and_ha_is_still_reconciled(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, network: FakeIox, fake_ha: FakeHa, tmp_path: Path, repo: Path
) -> None:
    network.refuse.add("RestoreDeviceFromNode")

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, repo, apply=True)

    assert ("ReplaceModem", {}) in network.writes()
    assert "dimmer" in fake_ha.removed()
    assert "failed: 11.22.33 Example Keypad: SOAP RestoreDevice: HTTP 500" in render(result)
