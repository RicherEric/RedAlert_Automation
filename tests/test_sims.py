"""Sanity tests for the simulators. Requires data/ (run extract.py first)."""
from pathlib import Path

import pytest

from ra2tools.combat import simulate
from ra2tools.economy import EconomySim
from ra2tools.gamedata import GameData
from ra2tools.optimize import Evaluation
from ra2tools.params import Calibration
from ra2tools.rules import ARMOR_TYPES

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


def test_home_ore_runs_out(db):
    cal = Calibration(ore_distance=10, harvest_frames_per_bale=28.8, unload_frames=285, speed_to_cells=0.009)
    sim = EconomySim(db, "Russians", cal)
    order = ["NAPOWR", "NAREFN", "HARV", "HARV"]
    full = sim.run(order, {"APOC": 50}, max_frames=30000)
    gone = EconomySim(db, "Russians", Calibration(**{**cal.__dict__, "ore_home_credits": 8000,
                                                     "ore_far_distance": 40})).run(order, {"APOC": 50}, max_frames=30000)
    assert gone.income < full.income
    half = EconomySim(db, "Russians", Calibration(**{**cal.__dict__, "harvest_efficiency": 0.5}))
    assert half.run(order, {"APOC": 50}, max_frames=6000).income < sim.run(order, {"APOC": 50}, max_frames=6000).income


def test_short_game_continues_as_the_longest():
    from ra2tools.opponent import Opponent, extend_with
    mk = lambda name, frames, ns: Opponent(name, "Americans", "Russians", frames, [{"MTNK": n} for n in ns],
                                           [{}] * len(frames), 70.0, attacks=[{"frame": f, "units": {}} for f in frames])
    short, long = mk("short", [0, 1000], [2, 2]), mk("long", [0, 1000, 2000, 3000], [9, 9, 12, 5])
    g = extend_with(short, long)
    # the short game keeps its own 2 tanks and gains what the long one added after frame 1000
    assert g.at(500)[0] == {"MTNK": 2} and g.at(2500)[0] == {"MTNK": 5} and g.at(3500)[0] == {}
    assert [a["frame"] for a in g.attacks] == [0, 1000, 2000, 3000]


def test_hardened_waves_are_bigger_and_sooner():
    from ra2tools.opponent import Opponent, harden
    g = Opponent("g", "Americans", "Russians", [0, 1000], [{}, {}], [{}, {}], 70.0,
                 attacks=[{"frame": 5000, "units": {"MTNK": 4, "E1": 1}}], raids=[{"frame": 800, "units": {"TANY": 1}}])
    h = harden(g, 1.2, 1000)
    assert h.attacks == [{"frame": 4000, "units": {"MTNK": 5, "E1": 1}}]
    assert h.raids == [{"frame": 0, "units": {"TANY": 1}}]
    assert g.attacks[0]["frame"] == 5000 and harden(g) is g


def test_industrial_plant_discounts_vehicles(db):
    assert db.technos["NAINDP"].cost_bonus["vehicle"] == 0.75
    sim = EconomySim(db, "Russians", Calibration(start_credits=100000))
    base = {"NACNST": 1, "NAPOWR": 3, "NAREFN": 1, "NAHAND": 1, "NAWEAP": 1, "NARADR": 1, "NATECH": 1}

    def build(extra):
        st = sim.initial_state()
        st.owned.clear()
        st.owned.update({**base, **extra})
        return sim.run(["HTNK"] * 4, {"HTNK": 4}, state=st, auto_power=False)
    plain, plant = build({}), build({"NAINDP": 1})
    assert plant.spent == pytest.approx(0.75 * plain.spent, rel=0.01)
    # plant_speeds_build: time comes from the discounted price
    assert plant.finish_frame - plant.events[0][0] <= 0.8 * (plain.finish_frame - plain.events[0][0])
    assert sim.cost_mult({"NAINDP": 1}, db.technos["E2"]) == 1.0  # infantry full price


def test_special_weapons_have_plain_stand_ins(db):
    # Before 2026-11-08 these counted as 0 damage and the search never picked them.
    w = lambda u: {x.approx: x for x in db.technos[u].combat_weapons if x.approx}
    v3, drone, dog, ivan = w("V3")["V3 rocket"], w("DRON")["parasite"], w("DOG")["dog bite"], w("IVAN")["Ivan bomb"]
    assert v3.range == 18 and v3.damage == 200 and v3.rof == 400
    plate, light, concrete = (ARMOR_TYPES.index(a) for a in ("plate", "light", "concrete"))
    assert drone.verses[light] > 0 and drone.verses[plate] == 0      # vehicles, not infantry
    assert dog.verses[plate] > 0 and dog.verses[light] == 0          # infantry only
    assert ivan.verses[concrete] > 0 and ivan.damage == 450
    strike = w("BORIS")["airstrike"]
    assert strike.verses[concrete] > 0 and strike.verses[plate] == 0  # the laser marks buildings
    assert simulate(db, {"V3": 4}, {"GAPILL": 2}).winner == "A"       # outranges the pillboxes
