"""Base upkeep (Steward), live adaptation (adapt), map fields and the economy's oil derricks."""
import math
from collections import Counter
from pathlib import Path

import pytest

from ra2tools.gamedata import GameData
from ra2tools.memory import FactoryState, HouseState, ObjectState, Snapshot

DATA = Path(__file__).resolve().parent.parent / "data"
ME, AI, NEUTRAL = 0, 1, 2


@pytest.fixture(scope="module")
def db():
    return GameData.load(DATA)


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a)) or "ok"


def obj(addr, tid, owner, x, y, health=100, kind=None, db=None):
    kind = kind or (db.technos[tid].kind if db and tid in db.technos else "building")
    return ObjectState(addr, kind, tid, owner, health, x, y, True, False)


def snap(frame, objects, credits=5000, factories=()):
    houses = [HouseState(1, ME, "Russians", True, True, False, credits, 0, 0),
              HouseState(2, AI, "Americans", False, False, False, 0, 0, 0),
              HouseState(3, NEUTRAL, "Neutral", False, False, False, 0, 0, 0)]
    return Snapshot(0.0, frame, 30, ME, houses, list(objects), list(factories))


def test_repairs_most_important_damaged_building_first(db):
    from ra2tools.steward import Steward
    bridge = Recorder()
    st = Steward(db, bridge, "Russians", garrison=0, max_repairs=1)
    yard, power = db.technos["NACNST"].strength, db.technos["NAPOWR"].strength
    st.tick(snap(100, [obj(1, "NAPOWR", ME, 10, 10, int(power * 0.5), "building"),
                       obj(2, "NACNST", ME, 12, 10, int(yard * 0.7), "building")]))
    assert bridge.calls == [("repair", 2)]
    # Healed: the game stops by itself, so the slot frees up for the power plant.
    st.tick(snap(200, [obj(1, "NAPOWR", ME, 10, 10, int(power * 0.5), "building"),
                       obj(2, "NACNST", ME, 12, 10, yard, "building")]))
    assert bridge.calls[-1] == ("repair", 1)


def test_no_repair_when_broke(db):
    from ra2tools.steward import Steward
    bridge = Recorder()
    st = Steward(db, bridge, "Russians", garrison=0)
    st.tick(snap(100, [obj(2, "NACNST", ME, 12, 10, 100, "building")], credits=100))
    assert not bridge.calls


def test_engineer_goes_for_nearest_neutral_derrick(db):
    from ra2tools.steward import Steward
    bridge = Recorder()
    st = Steward(db, bridge, "Russians", garrison=0)
    objs = [obj(1, "NACNST", ME, 10, 10, 1000, "building"), obj(5, "SENGINEER", ME, 11, 11, 75, "infantry"),
            obj(7, "CAOILD", NEUTRAL, 15, 20, 1000, "building"), obj(8, "CAOILD", NEUTRAL, 60, 60, 1000, "building"),
            obj(9, "CAAIRP", NEUTRAL, 12, 14, 800, "building")]
    st.tick(snap(100, objs))
    assert bridge.calls == [("capture", 5, 7, "d")]
    assert 5 in st.claimed
    st.tick(snap(1100, objs))  # still not taken after 900 frames: again, the target in another slot
    assert bridge.calls[-1] == ("capture", 5, 7, "td")


def test_idle_harvester_goes_to_gems(db):
    from ra2tools.mapdata import Field
    from ra2tools.steward import Steward
    bridge = Recorder()
    gems = Field("gem", [(40, 40, 10), (41, 40, 12)], 50)
    ore = Field("ore", [(35, 35, 12)], 25)  # nearer, but half the value per bale
    st = Steward(db, bridge, "Russians", fields=[ore, gems], garrison=0)
    base = [obj(1, "NACNST", ME, 10, 10, 1000, "building"), obj(2, "NAREFN", ME, 12, 12, 1000, "building")]
    harv = obj(3, "HARV", ME, 25, 25, 1000, "vehicle")
    for f in (100, 400, 700):
        st.tick(snap(f, base + [harv]))
    assert bridge.calls == [("harvest", 3, (41, 40))]  # gems a bit farther still win


def test_garrison_nearest_civilian_building(db):
    from ra2tools.steward import Steward
    bridge = Recorder()
    st = Steward(db, bridge, "Russians", garrison=2)
    objs = [obj(1, "NACNST", ME, 10, 10, 1000, "building"), obj(20, "CATEXS07", NEUTRAL, 18, 18, 1000, "building"),
            obj(21, "CATEXS06", NEUTRAL, 30, 30, 1000, "building")] + \
        [obj(100 + i, "E2", ME, 10 + i, 12, 125, "infantry") for i in range(4)]
    st.tick(snap(100, objs))
    assert [c[0] for c in bridge.calls] == ["capture", "capture"] and {c[2] for c in bridge.calls} == {20}
    assert len(st.claimed) == 2


def test_counter_pick_adds_anti_air_against_aircraft(db):
    from ra2tools.adapt import counter_pick
    from ra2tools.params import Calibration
    from ra2tools.problems import CombatSettings
    cal = Calibration(speed_to_cells=0.009154)
    add = counter_pick(db, cal, CombatSettings(focus=True), {"HTNK": 4}, {"ORCA": 4},
                       ["HTNK", "FLAKT", "HTK"], budget=4000)
    assert add and set(add) <= {"FLAKT", "HTK"}  # tanks cannot shoot aircraft


def test_engineer_captures_derrick_in_economy(db):
    from ra2tools.economy import EconomySim
    from ra2tools.params import Calibration
    cal = Calibration(derricks=[10.0])
    sim = EconomySim(db, "Russians", cal)
    with_eng = sim.run(["NAPOWR", "NAREFN", "NAHAND", "SENGINEER", "NAWEAP"], {"NAWEAP": 1, "SENGINEER": 1})
    plain = EconomySim(db, "Russians", Calibration()).run(["NAPOWR", "NAREFN", "NAHAND", "SENGINEER", "NAWEAP"],
                                                          {"NAWEAP": 1, "SENGINEER": 1})
    assert with_eng.owned["CAOILD"] == 1 and not plain.owned["CAOILD"]
    assert with_eng.income >= plain.income + db.technos["CAOILD"].cash_startup


def test_live_state_counts_derrick_as_its_engineer(db):
    from ra2tools.economy import EconomySim
    from ra2tools.env import state_from_snapshot
    from ra2tools.params import Calibration
    sim = EconomySim(db, "Russians", Calibration(derricks=[10.0, 50.0]))
    s = snap(3000, [obj(1, "NACNST", ME, 10, 10, 1000, "building"), obj(2, "NAHAND", ME, 12, 10, 500, "building"),
                    obj(7, "CAOILD", ME, 15, 20, 1000, "building")])
    state = state_from_snapshot(sim, s)
    assert state.owned["SENGINEER"] == 1 and state.derricks_sent == 1
    assert any(h[1] == db.technos["CAOILD"].cash_amount for h in state.harvesters)


def test_map_fields_of_skirmish_map(db):
    from ra2tools.mapdata import load_fields
    fields = load_fields()
    if not fields:
        pytest.skip("game data not installed")
    kinds = Counter(f.kind for f in fields)
    assert kinds["gem"] >= 1 and kinds["ore"] >= 1
    home = min(fields, key=lambda f: (f.centre[0] - 65) ** 2 + (f.centre[1] - 20) ** 2)
    assert home.kind == "ore" and 8000 < home.credits < 14000  # ~ the fitted ore_home_credits


def test_lcw_round_trip_literals_and_copies():
    from ra2tools.mapdata import lcw_decode
    # 0x83 = copy 3 literal bytes; 0x10 0x03 = copy 4 from 3 back (relative); 0xFE fill; 0x80 end
    src = bytes([0x83]) + b"abc" + bytes([0x10, 0x03, 0xFE, 0x02, 0x00, ord("z"), 0x80])
    assert lcw_decode(src, 64) == b"abcabca" + b"zz"


def test_storms_push_back_finish(db):
    from types import SimpleNamespace
    from ra2tools.problems import VictoryProblem
    p = VictoryProblem.__new__(VictoryProblem)
    p.db, p.country = db, "Russians"
    opp = SimpleNamespace(frames=[0, 20000], buildings=[{}, {"GAWEAT": 1}])
    assert p.storms(opp)[0] == 20000 + VictoryProblem.STORM_FIRST
    finish, hits = p.stormed(40000, opp)
    assert hits >= 1 and finish > 40000
    assert p.stormed(30000, opp) == (30000, 0)


def test_assault_focus_prefers_target_others_are_killing(db):
    from ra2tools.assault import Assault
    a = Assault(db, Recorder(), AI)
    u1, u2 = obj(1, "HTNK", ME, 0, 0, 400, "vehicle"), obj(2, "HTNK", ME, 0, 1, 400, "vehicle")
    t1, t2 = obj(10, "MTNK", AI, 3, 0, 300, "vehicle"), obj(11, "MTNK", AI, 3, 1, 300, "vehicle")
    a._target = {1: 10}
    a._commitments([u1, u2], [t1, t2])
    assert a._choose(u2, [t1, t2]).address == 10  # join the attack on the same tank


def test_rebuilds_lost_factory_after_the_build(db):
    from ra2tools.steward import Steward
    bridge = Recorder()
    st = Steward(db, bridge, "Russians", garrison=0)
    base = [obj(1, "NACNST", ME, 10, 10, 1000, "building"), obj(2, "NAPOWR", ME, 12, 10, 750, "building")]
    st.tick(snap(100, base + [obj(3, "NAWEAP", ME, 14, 10, 1000, "building")]))
    st.tick(snap(200, base))  # the war factory is gone, but the build policy still owns the queues
    assert not [c for c in bridge.calls if c[0] == "produce"]
    st.rebuild = True
    st.tick(snap(300, base))
    assert ("produce", "NAWEAP") in bridge.calls


def test_raider_at_home_does_not_start_the_attack(db):
    from ra2tools.assault import Assault
    a = Assault(db, Recorder(), AI)
    base = [obj(50, "GACNST", AI, 52, 97, 1000, "building")]
    army = [obj(1, "HTNK", ME, 65, 20, 400, "vehicle"), obj(2, "ZEP", ME, 66, 21, 2000, "aircraft")]
    raider = obj(60, "MTNK", AI, 66, 22, 300, "vehicle")
    s = snap(1000, army + base + [raider])
    assert not a._gather(s, a.army(s), a.enemies(s))  # still gathering
    # a fight at the rally point does start it
    rx, ry = a.rally
    army = [obj(1, "HTNK", ME, rx, ry, 400, "vehicle"), obj(2, "ZEP", ME, rx + 1, ry, 2000, "aircraft")]
    s = snap(1100, army + base + [obj(60, "MTNK", AI, rx + 2, ry, 300, "vehicle")])
    assert a._gather(s, a.army(s), a.enemies(s))


def test_rally_point_backs_off_from_the_ai_army(db):
    from ra2tools.assault import Assault
    a = Assault(db, Recorder(), AI)
    base = [obj(50, "GACNST", AI, 52, 97, 1000, "building")]
    army = [obj(1, "HTNK", ME, 52, 20, 400, "vehicle")]
    camp = [obj(60, "ATESLA", AI, 52, 80, 600, "building")]  # a gun near the default rally
    s = snap(1000, army + base + camp)
    x, y = a._rally_point(a.army(s), a.enemies(s))
    assert all(math.dist((x, y), (e.x, e.y)) > a.threat_range + a.gather_radius + 2 for e in camp)


def test_paradrop_plane_or_first_units_under_fire_do_not_start_the_attack(db):
    from ra2tools.assault import Assault
    a = Assault(db, Recorder(), AI)
    base = [obj(50, "GACNST", AI, 52, 97, 1000, "building")]
    army = [obj(1, "HTK", ME, 52, 20, 400, "vehicle"), obj(2, "ZEP", ME, 52, 21, 2000, "aircraft")]
    s = snap(1000, army + base)
    a._gather(s, a.army(s), a.enemies(s))
    rx, ry = a.rally
    # the fast AA tank is at the rally, the Kirov still far: a plane and a tank shoot at it
    army = [obj(1, "HTK", ME, rx, ry, 400, "vehicle"), obj(2, "ZEP", ME, 52, 40, 2000, "aircraft")]
    for t, f in (("PDPLANE", 1100), ("MTNK", 1200)):
        s = snap(f, army + base + [obj(60, t, AI, rx + 2, ry, 300, db=db)])
        assert not a._gather(s, a.army(s), a.enemies(s)), t


def test_kirov_bombs_the_anti_air_first(db):
    from ra2tools.assault import Assault
    a = Assault(db, Recorder(), AI)
    kirov = obj(1, "ZEP", ME, 50, 50, 2000, "aircraft")
    rifle = obj(10, "E1", AI, 52, 50, 125, "infantry")
    sam = obj(11, "NASAM", AI, 53, 51, db.technos["NASAM"].strength, "building")
    assert a._choose(kirov, [rifle, sam]).address == 11


def test_kirov_leaves_moving_units_for_the_buildings(db):
    # Live 2026-11-06 the Kirovs chased tanks and GIs across the AI base and razed nothing.
    from ra2tools.assault import Assault
    a = Assault(db, Recorder(), AI)
    kirov = obj(1, "ZEP", ME, 50, 50, 2000, "aircraft")
    gi = obj(10, "GGI", AI, 51, 50, 100, "infantry")
    tank = obj(11, "MTNK", AI, 51, 51, 300, "vehicle")
    pill = obj(12, "GAPILL", AI, 56, 50, 400, "building")
    power = obj(13, "GAPOWR", AI, 54, 50, 750, "building")
    sam = obj(14, "NASAM", AI, 58, 52, 900, "building")
    assert a._choose(kirov, [gi, tank, pill, power, sam]).address == 14  # the one that shoots it
    assert a._choose(kirov, [gi, tank, pill, power]).address == 12      # then the nearest armed one
    assert a._choose(kirov, [gi, tank]).address in (10, 11)              # no buildings left: units


def test_fast_tanks_hold_for_the_apocalypses(db):
    # Live 2026-11-06 (game 14) the Rhinos drove ahead of the Apocalypses into the Prism Towers.
    from ra2tools.assault import Assault
    rec = Recorder()
    a = Assault(db, rec, AI)
    apocs = [obj(i, "APOC", ME, 50 + i, 50, 800, "vehicle") for i in (1, 2, 3)]
    ahead = obj(10, "HTNK", ME, 52, 62, 400, "vehicle")   # 12 cells nearer the base
    beside = obj(11, "HTNK", ME, 54, 51, 400, "vehicle")
    prism = obj(20, "ATESLA", AI, 52, 80, 600, "building")
    yard = obj(21, "GACNST", AI, 52, 84, 1000, "building")
    a._engage(snap(100, []), [*apocs, ahead, beside], [prism, yard])
    moved = {c[1] for c in rec.calls if c[0] == "move"}
    attacked = {c[1] for c in rec.calls if c[0] == "attack"}
    assert 10 in moved and 10 not in attacked
    assert 11 in attacked and {1, 2, 3} <= attacked
    rec.calls.clear()
    tank = obj(10, "HTNK", ME, 52, 66, 400, "vehicle")  # a target in its own range: it fights
    gi = obj(22, "E1", AI, 52, 70, 125, "infantry")
    a._engage(snap(200, []), [*apocs, tank, beside], [gi, prism, yard])
    assert ("attack", 10, 22) in rec.calls


def test_leading_units_do_not_chase_into_the_towers(db):
    # Live 2026-11-07 the flak tracks chased Harriers and Grizzlies into the Prism Towers.
    from ra2tools.assault import Assault
    rec = Recorder()
    a = Assault(db, rec, AI)
    apocs = [obj(i, "APOC", ME, 50 + i, 50, 800, "vehicle") for i in (1, 2, 3)]
    prism = obj(20, "ATESLA", AI, 52, 80, 600, "building")
    yard = obj(21, "GACNST", AI, 52, 84, 1000, "building")
    flak = obj(10, "HTK", ME, 52, 66, 300, "vehicle")
    grizzly = obj(22, "MTNK", AI, 52, 74, 300, "vehicle")  # in sight, not in reach
    a._engage(snap(100, []), [*apocs, flak], [grizzly, prism, yard])
    assert any(c[0] == "move" and c[1] == 10 for c in rec.calls)
    assert not any(c[0] == "attack" and c[1] == 10 for c in rec.calls)
    rec.calls.clear()
    flak = obj(10, "HTK", ME, 52, 75, 300, "vehicle")  # under the Prism Tower: back, even with a target
    a._engage(snap(200, []), [*apocs, flak], [obj(23, "E1", AI, 52, 77, 125, "infantry"), prism, yard])
    assert any(c[0] == "move" and c[1] == 10 for c in rec.calls)
    assert not any(c[0] == "attack" and c[1] == 10 for c in rec.calls)


def test_rebuilder_spends_the_bank_on_the_plan_mix(db):
    from ra2tools.adapt import Rebuilder
    bridge = Recorder()
    rb = Rebuilder(db, bridge, {"APOC": 8, "BORIS": 1, "E2": 2, "HTK": 4})
    assert "BORIS" not in rb.plan  # BuildLimit: the game refuses a second one
    rb.tick(snap(100, [], credits=10000), Counter({"HTK": 2, "E2": 1}))
    assert ("produce", "APOC") in bridge.calls and ("produce", "E2") in bridge.calls
    bridge.calls.clear()
    rb.tick(snap(200, [], credits=10000, factories=[FactoryState("vehicle", "APOC", 0.5, False)]), Counter())
    assert bridge.calls == [("produce", "E2")]
    assert not rb.broke(snap(300, [], credits=10000))
    assert rb.broke(snap(300, [], credits=1550))
    assert not rb.broke(snap(300, [], credits=1550, factories=[FactoryState("vehicle", "HTK", 0.5, False)]))


def test_boris_lasers_the_towers_for_airstrikes(db):
    # Live series_v7 Boris duelled GIs and pillboxes with his rifle; the MiGs came only when
    # nothing else was left to shoot.
    from ra2tools.assault import Assault
    rec = Recorder()
    a = Assault(db, rec, AI)
    assert a.designator("BORIS") and not a.designator("E2")
    boris = obj(1, "BORIS", ME, 52, 60, 200, "infantry")
    gi = obj(20, "E1", AI, 52, 69, 125, "infantry")       # 9 cells: not his business
    prism = obj(21, "ATESLA", AI, 52, 74, 600, "building")
    yard = obj(22, "GACNST", AI, 52, 70, 1000, "building")
    a._engage(snap(100, []), [boris], [gi, prism, yard])
    assert rec.calls == [("attack", 1, 21)]
    a._engage(snap(200, []), [boris], [gi, prism, yard])
    assert len(rec.calls) == 1  # same target: no new order, the laser keeps going
    rec.calls.clear()
    near = obj(23, "E1", AI, 52, 64, 125, "infantry")     # 4 cells: shoot back
    a._engage(snap(300, []), [boris], [near, gi, prism, yard])
    assert rec.calls == [("attack", 1, 23)]
    rec.calls.clear()
    a._engage(snap(400, []), [boris], [gi, yard])           # the tower is gone: the yard
    assert rec.calls == [("attack", 1, 22)]


def test_reinforcer_buys_a_guard_for_the_chrono_drop(db):
    from ra2tools.adapt import Reinforcer
    from ra2tools.params import Calibration
    from ra2tools.problems import CombatSettings
    rec = Recorder()
    r = Reinforcer(db, rec, Calibration(speed_to_cells=0.009154), CombatSettings(focus=True), AI,
                   ["HTNK", "APOC", "HTK"], home_threat={"MTNK": 4, "SREF": 1, "MGTK": 1})
    r.tick(snap(1000, [], credits=6000))  # the AI fields nothing now: only the drop asks for units
    assert any(c[0] == "produce" and c[1] in ("HTNK", "APOC") for c in rec.calls)


def test_rebuild_moves_on_when_a_prerequisite_is_gone_too(db):
    # Live 2026-11-09 (series_v10 game 2): radar and tech centre both lost; the tech centre's
    # rebuild could never start and nothing else (radar, power, barracks) was rebuilt.
    from ra2tools.bridge import BridgeError
    from ra2tools.steward import REBUILD_START, Steward

    class NoTech(Recorder):
        def produce(self, tid):
            if tid == "NATECH":
                raise BridgeError("cannot build NATECH now (CanBuild=0)")
            self.calls.append(("produce", tid))
            return "ok"

    bridge = NoTech()
    st = Steward(db, bridge, "Russians", garrison=0, rebuild=True)
    base = [obj(1, "NACNST", ME, 10, 10, 1000), obj(2, "NAPOWR", ME, 12, 10, 750), obj(3, "NAWEAP", ME, 14, 10),
            obj(4, "NAREFN", ME, 16, 10)]
    st.tick(snap(100, base + [obj(5, "NARADR", ME, 18, 10), obj(6, "NATECH", ME, 20, 10)], credits=20000))
    for f in range(200, 200 + REBUILD_START + 200, 30):
        st.tick(snap(f, base, credits=20000))
    assert ("produce", "NARADR") in bridge.calls


def test_rebuilder_falls_back_to_what_the_factory_can_make(db):
    from ra2tools.adapt import Rebuilder
    from ra2tools.bridge import BridgeError

    class NoTech(Recorder):
        def produce(self, tid):
            if tid == "APOC":
                raise BridgeError("cannot build APOC now (CanBuild=0)")
            self.calls.append(("produce", tid))
            return "ok"

    bridge = NoTech()
    rb = Rebuilder(db, bridge, {"APOC": 9, "HTK": 5})
    rb.tick(snap(100, [], credits=20000), Counter({"HTK": 3}))
    assert bridge.calls == [("produce", "HTK")]


def test_rebuilder_stops_each_type_at_its_plan_count(db):
    # Live 2026-11-09 (series_v10 game 4): wave 2 left with 83 conscripts and 4 Apocalypses.
    from ra2tools.adapt import Rebuilder
    bridge = Recorder()
    rb = Rebuilder(db, bridge, {"APOC": 9, "E2": 2})
    rb.tick(snap(100, [], credits=20000, factories=[FactoryState("vehicle", "APOC", 0.5, False)]),
            Counter({"E2": 2, "APOC": 3}))
    assert bridge.calls == []  # the barracks stay idle: two conscripts are the plan


def test_rebuilder_adds_war_factories_with_a_full_bank(db):
    # Live 2026-11-09 (series_v10 game 5) $22k sat unspent through every regroup.
    from ra2tools.adapt import Rebuilder
    yard, weap = obj(1, "NACNST", ME, 60, 20), obj(2, "NAWEAP", ME, 66, 20)
    bridge = Recorder()
    rb = Rebuilder(db, bridge, {"APOC": 8}, factory="NAWEAP")
    rb.tick(snap(100, [yard, weap], credits=3000), Counter())
    assert ("produce", "NAWEAP") not in bridge.calls  # not twice its price to spare
    rb.tick(snap(200, [yard, weap], credits=20000), Counter())
    assert ("produce", "NAWEAP") in bridge.calls and rb._adding is not None
    full = Rebuilder(db, Recorder(), {"APOC": 8}, factory="NAWEAP")
    four = [yard] + [obj(10 + i, "NAWEAP", ME, 66, 20 + 4 * i) for i in range(4)]
    full.tick(snap(100, four, credits=20000), Counter())
    assert ("produce", "NAWEAP") not in full.commander.calls


def test_gather_sends_a_stuck_unit_aside_and_holds_runaways(db):
    """series_v11 game 1: vehicles sat at home for 3800 frames while the infantry ran ahead."""
    from ra2tools.assault import STUCK_FRAMES, Assault
    rec = Recorder()
    a = Assault(db, rec, AI)
    base = [obj(50, "GACNST", AI, 52, 97, 1000, "building")]
    tanks = [obj(i, "APOC", ME, 66, 20 + i, 800, "vehicle") for i in range(1, 5)]
    s = snap(1000, tanks + base + [obj(9, "E2", ME, 66, 26, 125, "infantry")])
    a._gather(s, a.army(s), a.enemies(s))
    rally = a.rally
    assert sum(1 for c in rec.calls if c[0] == "move" and c[2] == rally) == 5
    assert [m[4] for m in a.move_log] == ["ok"] * 5
    # the tanks never move; the conscript walks far ahead of them
    rec.calls.clear()
    f = 1000 + STUCK_FRAMES + 10
    e2 = obj(9, "E2", ME, rally[0], rally[1] - 15, 125, "infantry")
    s = snap(f, tanks + base + [e2])
    a._gather(s, a.army(s), a.enemies(s))
    moves = {c[1]: c[2] for c in rec.calls if c[0] == "move"}
    assert all(moves[i] != rally for i in range(1, 5))                # each tank sent beside itself
    assert all(math.dist(moves[i], (66, 20 + i)) <= 4.5 for i in range(1, 5))
    assert moves[9] == (round(e2.x), round(e2.y))                      # the conscript holds


def test_gather_logs_bridge_errors(db):
    from ra2tools.assault import Assault
    from ra2tools.bridge import BridgeError

    class Full(Recorder):
        def move(self, *a):
            raise BridgeError("event queue full")

    a = Assault(db, Full(), AI)
    base = [obj(50, "GACNST", AI, 52, 97, 1000, "building")]
    s = snap(1000, [obj(1, "APOC", ME, 66, 20, 800, "vehicle")] + base)
    a._gather(s, a.army(s), a.enemies(s))
    assert [m[:5] for m in a.move_log] == [(1000, "APOC", *a.rally, "event queue full")]
    assert 1 not in a._ordered_at  # retried on the next tick


def test_reinforcer_does_not_claim_units_made_while_it_was_paused(db):
    # Live 2026-11-10 (series_v12 game 2): it resumed after a regroup and took the rebuilt wave.
    from ra2tools.adapt import Reinforcer
    from ra2tools.params import Calibration
    from ra2tools.problems import CombatSettings
    r = Reinforcer(db, Recorder(), Calibration(speed_to_cells=0.009154), CombatSettings(focus=True), AI,
                   ["APOC"], reserve=10**9)
    tank = lambda a: obj(a, "APOC", ME, 10, 10, db=db)
    r._queued = {"vehicle": "APOC"}
    r.tick(snap(1000, [tank(1)], credits=0))
    r._queued = {"vehicle": "APOC"}
    r.tick(snap(1030, [tank(1), tank(2)], credits=0))  # came out while it watched: its own
    assert r.units == {2}
    r._queued = {"vehicle": "APOC"}
    r.tick(snap(9000, [tank(1), tank(2)] + [tank(a) for a in range(3, 20)], credits=0))  # after a pause
    assert r.units == {2}
