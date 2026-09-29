"""The Frigate config's driveway zone covers where cars actually stand.

Frigate puts an object in a zone when the bottom centre of its bounding box
is inside the zone's polygon. The points below are observed bottom centres
from Frigate car events on the front-door camera, normalised to the frame.
"""

from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).parent.parent / "frigate" / "config.yaml"
# Frigate's detect resolution for the camera; zone coordinates are pixels at this size.
DETECT_WIDTH, DETECT_HEIGHT = 896, 672

ARRIVING_CAR = [
    (0.18, 0.57),  # left end of the driveway
    (0.36, 0.55),  # middle of the driveway
    (0.35, 0.609),  # nearest the camera
    (0.62, 0.57),  # right end of the driveway
    (0.30, 0.52),  # far edge of the driveway
]
# A vehicle parked in street parking, which Frigate detects as a car over and
# over. A car in street parking is never an Alert (front-door/CONTEXT.md).
STREET_PARKING = [(0.095, 0.60), (0.135, 0.58)]


def _driveway() -> list[tuple[float, float]]:
    config = yaml.safe_load(CONFIG.read_text())
    pixels = [int(v) for v in config["cameras"]["front_door"]["zones"]["driveway"]["coordinates"].split(",")]
    return [(x / DETECT_WIDTH, y / DETECT_HEIGHT) for x, y in zip(pixels[::2], pixels[1::2])]


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
