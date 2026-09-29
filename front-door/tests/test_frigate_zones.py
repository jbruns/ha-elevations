"""The Frigate config's zones cover where people and cars actually are.

Frigate puts an object in a zone when the bottom centre of its bounding box
is inside the zone's polygon. The points below are observed bottom centres
from Frigate car events on the front-door camera, normalised to the frame.
"""

from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).parent.parent / "frigate" / "config.yaml"

ARRIVING_CAR = [
    (0.18, 0.57),  # left end of the driveway
    (0.36, 0.55),  # middle of the driveway
    (0.35, 0.609),  # nearest the camera
    (0.62, 0.57),  # right end of the driveway
    (0.30, 0.52),  # far edge of the driveway
]
# People on the porch, from Frigate person events in entry_breezeway.
PERSON_ON_PORCH = [
    (0.65, 0.62),  # near the front door
    (0.72, 0.70),  # middle of the porch
    (0.90, 0.72),  # right side of the porch
]
# A vehicle parked in street parking, which Frigate detects as a car over and
# over. A car in street parking is never an Alert (front-door/CONTEXT.md).
STREET_PARKING = [(0.095, 0.60), (0.135, 0.58)]


def _camera() -> dict:
    return yaml.safe_load(CONFIG.read_text())["cameras"]["front_door"]


def _polygon(coordinates: str) -> list[tuple[float, float]]:
    values = [float(v) for v in coordinates.split(",")]
    return list(zip(values[::2], values[1::2]))


def _driveway() -> list[tuple[float, float]]:
    return _polygon(_camera()["zones"]["driveway"]["coordinates"])


def _all_coordinates() -> list[tuple[str, str]]:
    camera = _camera()
    found = [(f"zone {name}", zone["coordinates"]) for name, zone in camera["zones"].items()]
    found += [(f"motion mask {name}", mask["coordinates"]) for name, mask in camera["motion"]["mask"].items()]
    return found


def _inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[-1:] + polygon[:-1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


@pytest.mark.parametrize("point", ARRIVING_CAR)
def test_a_car_on_the_driveway_is_in_the_driveway_zone(point: tuple[float, float]) -> None:
    assert _inside(point, _driveway())


@pytest.mark.parametrize("point", STREET_PARKING)
def test_a_car_in_street_parking_is_not_in_the_driveway_zone(point: tuple[float, float]) -> None:
    assert not _inside(point, _driveway())


@pytest.mark.parametrize(("name", "coordinates"), _all_coordinates())
def test_coordinates_are_fractions_of_the_frame(name: str, coordinates: str) -> None:
    # Frigate picks the detect resolution at startup unless it is pinned, so
    # pixel coordinates move whenever it picks a different one.
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in _polygon(coordinates)), name
    assert any("." in v for v in coordinates.split(",")), f"{name} looks like pixels"


@pytest.mark.parametrize("point", PERSON_ON_PORCH)
def test_a_person_on_the_porch_is_in_the_entry_breezeway_zone(point: tuple[float, float]) -> None:
    assert _inside(point, _polygon(_camera()["zones"]["entry_breezeway"]["coordinates"]))
