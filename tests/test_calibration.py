"""Fit must recover known parameters from synthetic stopwatch measurements."""
from pathlib import Path

import pytest

from ra2tools.calibration import Measurement, fit, marks_to_intervals
from ra2tools.gamedata import GameData

DATA = Path(__file__).parent.parent / "data"
pytestmark = pytest.mark.skipif(not (DATA / "rulesmd.ini").exists(), reason="run extract.py first")

FPS = 30.0
SPEED_TO_CELLS = 0.025
ORE_DISTANCE = 7.0
FRAMES_PER_BALE = 2.5
UNLOAD = 90.0


@pytest.fixture(scope="module")
def db():
    return GameData.load(DATA)


def test_marks_grouping():
    assert marks_to_intervals("build", [0, 5, 10, 16]) == [5, 6]
    assert marks_to_intervals("harvest", [0, 2, 5, 9]) == [2, 3, 4]


def test_fit_recovers_parameters(db):
    def sec(frames):
        return frames / FPS

    harv = db.technos["HARV"]
    travel = ORE_DISTANCE / (harv.speed * SPEED_TO_CELLS)
    cycle = [sec(travel), sec(harv.storage * FRAMES_PER_BALE), sec(travel), sec(UNLOAD)]
    ms = [
        Measurement("build", [sec(db.build_frames(db.technos["NAPOWR"]))], {"item": "NAPOWR"}),
        Measurement("build", [sec(db.build_frames(db.technos["NAREFN"]))] * 2, {"item": "NAREFN"}),
        Measurement("move", [sec(10 / (6 * SPEED_TO_CELLS))], {"unit": "HTNK", "cells": 10}),
        Measurement("harvest", cycle * 2, {"unit": "HARV"}),
        Measurement("place", [1.0, 2.0, 3.0]),
    ]
    r = fit(db, ms)
    v = r.values
    assert v["fps"] == pytest.approx(FPS)
    assert v["speed_to_cells"] == pytest.approx(SPEED_TO_CELLS)
    assert v["ore_distance"] == pytest.approx(ORE_DISTANCE)
    assert v["harvest_frames_per_bale"] == pytest.approx(FRAMES_PER_BALE)
    assert v["unload_frames"] == pytest.approx(UNLOAD)
    assert v["place_delay_frames"] == pytest.approx(2.0 * FPS)
    assert not any("警告" in n for n in r.notes)


def test_inconsistent_build_times_are_flagged(db):
    ms = [
        Measurement("build", [10.0], {"item": "NAPOWR"}),
        Measurement("build", [10.0], {"item": "NAREFN"}),  # 3.3x the cost in the same time
    ]
    assert any("建造時間公式" in n for n in fit(db, ms).notes)


def test_no_build_measurement_means_no_fit(db):
    r = fit(db, [Measurement("move", [5.0], {"unit": "HTNK", "cells": 10})])
    assert r.values == {}


def test_fit_log_recovers_fps_and_build_rate(db):
    from ra2tools.calibration import fit_log

    fps = 30.0
    full = db.build_frames(db.technos["HTNK"])  # frames for one Rhino at full speed
    records = []
    for i in range(40):
        frame = 1000 + i * 15
        progress = min(1.0, (i * 15) / full)
        records.append({"wall_time": 100 + i * 15 / fps, "frame": frame,
                        "factories": [{"category": "vehicle", "type_id": "HTNK",
                                       "progress": progress, "on_hold": False}]})
    # A pause: frame counter stops while wall time moves on; must not lower the fps.
    records.append({**records[-1], "wall_time": records[-1]["wall_time"] + 60})
    r = fit_log(db, records)
    assert r.values["fps"] == pytest.approx(fps)
    assert r.samples["build_ratio"][0] == pytest.approx(1.0, abs=0.01)


def test_fit_log_groups_fps_by_game_speed(db, tmp_path):
    import tomllib

    from ra2tools.calibration import fit_log, write_toml
    from ra2tools.params import Calibration

    records, wall, frame = [], 0.0, 0
    for speed, fps, n in ((3, 30.0, 40), (1, 60.0, 10)):
        for _ in range(n):
            records.append({"wall_time": wall, "frame": frame, "game_speed": speed, "factories": []})
            wall += 0.5
            frame += int(fps * 0.5)
    r = fit_log(db, records)
    assert r.fps_by_speed == {"3": pytest.approx(30.0), "1": pytest.approx(60.0)}
    assert r.values["fps"] == pytest.approx(30.0)  # played longest
    path = tmp_path / "cal.toml"
    write_toml(path, r)
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    assert Calibration.from_dict({**cfg, "game_speed": 1}).fps == pytest.approx(60.0)
    assert Calibration.from_dict(cfg).fps == pytest.approx(30.0)
