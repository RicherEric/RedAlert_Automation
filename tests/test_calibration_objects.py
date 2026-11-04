"""fit_objects must recover movement and harvest parameters from synthetic object tracks."""
from pathlib import Path

import pytest

from ra2tools.calibration import fit_objects
from ra2tools.gamedata import GameData

DATA = Path(__file__).parent.parent / "data"
pytestmark = pytest.mark.skipif(not (DATA / "rulesmd.ini").exists(), reason="run extract.py first")

STC = 0.025
STEP = 30  # frames between samples


@pytest.fixture(scope="module")
def db():
    return GameData.load(DATA)


def harvester_path(db, refinery, frames_per_bale, unload, distance):
    """Positions of a HARV doing two full cycles, one sample every STEP frames."""
    harv = db.technos["HARV"]
    v = harv.speed * STC
    travel = distance / v
    load = harv.storage * frames_per_bale
    cycle = [("unload", unload), ("out", travel), ("load", load), ("back", travel)]
    rx, ry = refinery

    def pos(f):
        f %= sum(d for _, d in cycle)
        for phase, d in cycle:
            if f < d:
                break
            f -= d
        if phase == "unload":
            return rx + 1, ry
        if phase == "out":
            return rx + 1 + f * v, ry
        if phase == "load":
            return rx + 1 + distance, ry
        return rx + 1 + distance - f * v, ry

    total = 2 * sum(d for _, d in cycle) + unload
    return [pos(f) for f in range(0, int(total), STEP)]


def test_fit_objects_recovers_movement_and_harvest(db):
    refinery = (50.0, 50.0)
    harv = harvester_path(db, refinery, frames_per_bale=3.0, unload=90, distance=12)
    # The AI's base mines slower; its trips must not leak into the player's calibration.
    enemy_ref = (20.0, 80.0)
    enemy = harvester_path(db, enemy_ref, frames_per_bale=6.0, unload=90, distance=8)
    tank_speed = db.technos["HTNK"].speed * STC
    records = []
    for i, (hx, hy) in enumerate(harv):
        frame = 1000 + i * STEP
        objs = [
            {"address": 1, "kind": "building", "type_id": "NAREFN", "owner": 0, "health": 900,
             "x": refinery[0], "y": refinery[1], "on_map": True, "selected": False},
            {"address": 2, "kind": "vehicle", "type_id": "HARV", "owner": 0, "health": 900,
             "x": hx, "y": hy, "on_map": True, "selected": False},
            {"address": 3, "kind": "vehicle", "type_id": "HTNK", "owner": 0, "health": 900,
             "x": 10 + min(i, 20) * STEP * tank_speed, "y": 10.0, "on_map": True, "selected": False},
            {"address": 4, "kind": "building", "type_id": "NAREFN", "owner": 1, "health": 900,
             "x": enemy_ref[0], "y": enemy_ref[1], "on_map": True, "selected": False},
            {"address": 5, "kind": "vehicle", "type_id": "HARV", "owner": 1, "health": 900,
             "x": enemy[i % len(enemy)][0], "y": enemy[i % len(enemy)][1], "on_map": True, "selected": False},
        ]
        records.append({"wall_time": i, "frame": frame, "player_index": 0, "factories": [],
                        "houses": [{"index": 0, "country": "Russians"}, {"index": 1, "country": "Russians"}],
                        "objects": objs})
    r = fit_objects(db, records)
    v = r.values
    assert v["speed_to_cells"] == pytest.approx(STC, rel=0.01)
    assert v["harvest_frames_per_bale"] == pytest.approx(3.0, rel=0.15)
    # Out-travel is measured from leaving the dock zone, so it is a bit short of the full distance;
    # the missing part is counted in the unload phase instead, keeping the cycle total right.
    cycle = (2 * v["ore_distance"] / (db.technos["HARV"].speed * STC)
             + v["harvest_frames_per_bale"] * db.technos["HARV"].storage + v["unload_frames"])
    true_cycle = 2 * 12 / (db.technos["HARV"].speed * STC) + 3.0 * db.technos["HARV"].storage + 90
    assert cycle == pytest.approx(true_cycle, rel=0.1)
