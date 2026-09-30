import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
WALLBOARD_LOCAL_ENTITIES = {
    "binary_sensor.wallboard_any_entry_unlocked",
    "sensor.wallboard_unlocked_entries",
    "binary_sensor.wallboard_washer_active",
    "binary_sensor.wallboard_dryer_active",
    "binary_sensor.wallboard_dishwasher_active",
}
ALLOWED_PREFIX = "dashboards/wallboard/"


def test_nothing_outside_wallboard_reads_wallboard_package_entities() -> None:
    offenders: list[str] = []
    tracked = subprocess.run(
        ["git", "ls-files", "*.yaml", "*.yml", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for rel in tracked:
        if rel == "dashboards/tests/test_display_detachability.py":
            continue
        if rel.startswith(ALLOWED_PREFIX):
            continue
        path = ROOT / rel
        text = path.read_text(errors="ignore")
        for entity in WALLBOARD_LOCAL_ENTITIES:
            if entity in text:
                offenders.append(f"{rel}: {entity}")
    assert offenders == []
