"""Home Assistant saves every imported blueprint as
blueprints/<domain>/<owner>/<filename>, dropping the repo path, so blueprint
filenames must be unique across the whole repo (ADR 0007)."""

from pathlib import Path

from testing.blueprints import blueprint_domain, blueprint_files, duplicate_filenames


def test_no_two_blueprints_in_the_repo_share_a_filename() -> None:
    assert duplicate_filenames(blueprint_files()) == {}


def test_the_repo_has_blueprints_to_check() -> None:
    names = {path.name for path in blueprint_files()}
    assert "front_door_alert_notifications.yaml" in names


def test_a_filename_shared_across_contexts_is_a_duplicate() -> None:
    first = Path("front-door/blueprints/automation/notifications.yaml")
    second = Path("safety/blueprints/automation/notifications.yaml")
    other = Path("climate/blueprints/automation/climate_comfort_policy.yaml")

    assert duplicate_filenames([first, second, other]) == {
        "notifications.yaml": [first, second]
    }


def test_a_filename_shared_across_domains_is_a_duplicate() -> None:
    automation = Path("safety/blueprints/automation/safety_leak.yaml")
    script = Path("safety/blueprints/script/safety_leak.yaml")

    assert duplicate_filenames([automation, script]) == {
        "safety_leak.yaml": [automation, script]
    }


def test_blueprints_are_found_in_every_context(tmp_path: Path) -> None:
    for path in [
        "front-door/blueprints/automation/front_door_a.yaml",
        "climate/blueprints/automation/climate_a.yaml",
        "climate/blueprints/automation/climate_b.yaml",
        "safety/blueprints/automation/leaks/safety_leak.yml",
        "front-door/dashboards/front_door_view.yaml",
        ".venv/lib/blueprints/automation/vendored.yaml",
    ]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).touch()

    assert sorted(p.relative_to(tmp_path).as_posix() for p in blueprint_files(tmp_path)) == [
        "climate/blueprints/automation/climate_a.yaml",
        "climate/blueprints/automation/climate_b.yaml",
        "front-door/blueprints/automation/front_door_a.yaml",
        "safety/blueprints/automation/leaks/safety_leak.yml",
    ]


def test_a_blueprint_in_a_subfolder_keeps_its_domain() -> None:
    path = Path("safety/blueprints/automation/leaks/safety_leak.yaml")
    assert blueprint_domain(path) == "automation"
