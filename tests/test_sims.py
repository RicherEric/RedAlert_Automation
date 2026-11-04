"""Sanity tests for the simulators. Requires data/ (run extract.py first)."""
from pathlib import Path

import pytest

from ra2tools.combat import simulate
from ra2tools.economy import EconomySim
from ra2tools.gamedata import GameData
from ra2tools.optimize import Evaluation
from ra2tools.params import Calibration

DATA = Path(__file__).parent.parent / "data"
pytestmark = pytest.mark.skipif(not (DATA / "rulesmd.ini").exists(), reason="run extract.py first")


@pytest.fixture(scope="module")
def db():
    return GameData.load(DATA)


def test_mirror_match_is_a_draw(db):
    r = simulate(db, {"HTNK": 4}, {"HTNK": 4})
    assert r.score == pytest.approx(0.0)


def test_rhino_beats_equal_count_grizzly(db):
    assert simulate(db, {"HTNK": 5}, {"MTNK": 5}).winner == "A"


def test_ground_only_army_cannot_hit_kirov(db):
    r = simulate(db, {"MTNK": 9}, {"ZEP": 1})
    assert r.winner == "B" and r.b_value_left == pytest.approx(1.0)


def test_tanks_crush_infantry(db):
    assert simulate(db, {"E2": 10}, {"HTNK": 2}, crush=True).winner == "B"


def test_stolen_tech_units_excluded(db):
    ids = {t.id for t in db.combat_units("Russians")}
    assert "CCOMAND" not in ids and "HTNK" in ids and "MTNK" not in ids


def test_power_plant_build_time_matches_formula(db):
    cal = Calibration()
    r = EconomySim(db, "Russians", cal).run(["NAPOWR"], {"NAPOWR": 1})
    expected = cal.mcv_deploy_frames + db.build_frames(db.technos["NAPOWR"]) + cal.place_delay_frames
    assert abs(r.finish_frame - expected) <= 15  # one simulation step


def test_resolve_inserts_prerequisites(db):
    seq = EconomySim(db, "Russians").resolve([], {"APOC": 1})
    for building in ("NAPOWR", "NAREFN", "NAHAND", "NAWEAP", "NARADR", "NATECH"):
        assert building in seq
    assert seq.index("NATECH") < seq.index("APOC")


def test_second_war_factory_speeds_up_production(db):
    sim = EconomySim(db, "Russians")
    one = sim.run([], {"HTNK": 8}).finish_frame
    two = sim.run(["NAWEAP", "HTNK", "NAWEAP"], {"HTNK": 8}).finish_frame
    assert two < one


def test_feasibility_rules():
    feasible = Evaluation(10.0)
    infeasible = Evaluation(-5.0, {"budget": 0.1})
    worse = Evaluation(-9.0, {"budget": 0.5})
    assert feasible.better_than(infeasible)
    assert infeasible.better_than(worse)


def test_build_frames_match_game(db):
    # Measured in a real skirmish (logs from monitor.py watch): 54 steps, whole frames per step.
    measured = {"GAPOWR": 486, "GAREFN": 1242, "GAPILE": 270, "AMRADR": 594, "ADOG": 108, "CMIN": 864,
                # cost 600: exactly 7 steps in exact maths, 6 with the engine's float32 BuildSpeed
                "NAPOWR": 324, "GAROBO": 324}
    for tid, frames in measured.items():
        assert db.build_frames(db.technos[tid]) == frames
