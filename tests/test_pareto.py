"""NSGA-II on a toy problem whose Pareto front is known."""
import random
from collections import Counter

from ra2tools.optimize import Evaluation, fronts, run_nsga2, tchebycheff


class Toy:
    """Genome (x, y) in 0..20: minimise (x + y, 20 - x + y). The front is y = 0, x = 0..20;
    with x <= 15 as a constraint it shrinks to x = 0..15."""

    def __init__(self, cap=None):
        self.cap = cap

    def key(self, g):
        return g

    def random_genome(self, rng):
        return rng.randint(0, 20), rng.randint(0, 20)

    def mutate(self, g, rng):
        x, y = g
        if rng.random() < 0.5:
            x = min(20, max(0, x + rng.choice((-2, -1, 1, 2))))
        else:
            y = min(20, max(0, y + rng.choice((-2, -1, 1))))
        return x, y

    def crossover(self, a, b, rng):
        return a[0], b[1]

    def evaluate(self, g):
        x, y = g
        v = {"cap": float(x - self.cap)} if self.cap is not None and x > self.cap else {}
        return Evaluation(0.0, v, objectives=(x + y, 20 - x + y))


def test_domination_and_sort():
    a, b, c = (Evaluation(0, objectives=o) for o in [(1, 1), (2, 2), (0, 3)])
    bad = Evaluation(0, {"v": 1.0}, objectives=(0, 0))
    assert a.dominates(b) and not a.dominates(c) and not c.dominates(a)
    assert c.dominates(bad)  # feasible beats infeasible whatever the objectives
    assert fronts([a, b, c, bad]) == [[0, 2], [1], [3]]


def test_nsga2_finds_whole_front():
    res = run_nsga2(Toy(), pop_size=40, generations=40, seed=1)
    xs = sorted(g[0] for g in res.front)
    assert all(g[1] == 0 for g in res.front)
    assert xs[0] == 0 and xs[-1] == 20 and len(xs) >= 15  # extremes kept by crowding distance


def test_nsga2_respects_constraints_and_preference():
    res = run_nsga2(Toy(cap=15), pop_size=40, generations=40, seed=2)
    assert all(e.feasible for e in res.evals) and max(g[0] for g in res.front) == 15
    first = res.front[tchebycheff(res.evals, [1.0, 0.0])]
    second = res.front[tchebycheff(res.evals, [0.0, 1.0])]
    middle = res.front[tchebycheff(res.evals, [1.0, 1.0])]
    assert first == (0, 0) and second == (15, 0) and 6 <= middle[0] <= 9


def test_mission_problem_scores_three_objectives():
    """Win margin, ready frame and money spent; a stronger army is slower and dearer."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.params import Calibration
    from ra2tools.problems import EnemyScenario, MissionProblem

    db = GameData.load(data)
    cal = Calibration()
    p = MissionProblem(db, "Russians", [EnemyScenario("grizzlies", {"MTNK": 4})], ["HTNK", "E2"], cal,
                       min_score=0.0, planner=PolicyPlanner(db, "Russians", cal))  # no model: resolve()
    small, big = p.evaluate((1, 0)), p.evaluate((8, 0))
    assert len(big.objectives) == 3 and big.feasible and not small.feasible  # 1 tank loses to 4
    assert big.objectives[0] < small.objectives[0]  # better margin
    assert big.objectives[1] > small.objectives[1] and big.objectives[2] > small.objectives[2]
    assert p.planner.calls == 2


def test_victory_problem_rewards_early_attack():
    """Against an opponent whose defences grow, the same army wins only if it arrives early."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    weak = {"GACNST": 1, "GAPOWR": 1}
    strong = {"GACNST": 1, "GAPOWR": 2, "ATESLA": 4}
    opp = Opponent("toy", "Americans", "Russians", [0, 8000], [{}, {"MTNK": 6}], [weak, strong], 40.0)
    assert opp.at(7999) == ({}, weak) and opp.at(9000)[1] == strong

    def problem(o):
        return VictoryProblem(db, "Russians", [], ["HTNK"], cal, planner=PolicyPlanner(db, "Russians", cal), opponent=o)

    early = problem(opp).evaluate((3,))
    assert early.feasible and early.info["arrive_frame"] < 8000
    assert early.objectives[0] == pytest.approx(early.info["arrive_frame"] + early.info["battle_frames"])
    late = problem(Opponent("late", "Americans", "Russians", [0], [{"MTNK": 6}], [strong], 40.0)).evaluate((3,))
    assert not late.feasible and "lose" in late.violations


def test_assault_targets_threats_then_buildings():
    from ra2tools.assault import Assault
    from ra2tools.memory import HouseState, ObjectState, Snapshot

    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData

    db = GameData.load(data)

    class Cmd:
        def __init__(self):
            self.log = []

        def attack(self, unit, target):
            self.log.append((unit, target))

        def move(self, unit, cell):
            self.log.append((unit, "move", cell))

    houses = [HouseState(1, 0, "Russians", True, True, False, 0, 0, 0),
              HouseState(2, 1, "Americans", False, False, False, 0, 0, 0)]
    tank = ObjectState(0x10, "vehicle", "HTNK", 0, 100, 0, 0, True, False)
    far_bld = ObjectState(0x20, "building", "GAPOWR", 1, 100, 30, 0, True, False)
    near_bld = ObjectState(0x21, "building", "GACNST", 1, 100, 20, 0, True, False)
    gi = ObjectState(0x30, "infantry", "E1", 1, 100, 5, 0, True, False)
    cmd = Cmd()
    a = Assault(db, cmd, enemy=1, gather_distance=0)
    a.tick(Snapshot(0, 100, 20, 0, houses, [tank, far_bld, near_bld]))
    assert cmd.log == [(0x10, 0x21)]  # the construction yard
    a.tick(Snapshot(0, 110, 20, 0, houses, [tank, far_bld, near_bld, gi]))
    assert cmd.log[-1] == (0x10, 0x30)  # an armed enemy close by comes first
    assert not a.defeated(Snapshot(0, 120, 20, 0, houses, [tank, gi, near_bld]))
    assert a.defeated(Snapshot(0, 130, 20, 0, houses, [tank, gi]))

    # The construction yard beats a nearer building; the weakest threat in range beats the nearest.
    power = ObjectState(0x22, "building", "GAPOWR", 1, 100, 10, 0, True, False)
    cy = ObjectState(0x23, "building", "GACNST", 1, 100, 40, 0, True, False)
    gi2 = ObjectState(0x31, "infantry", "E1", 1, 30, 8, 0, True, False)
    a = Assault(db, cmd, enemy=1, gather_distance=0)
    a.tick(Snapshot(0, 100, 20, 0, houses, [tank, power, cy]))
    assert cmd.log[-1] == (0x10, 0x23)
    a.tick(Snapshot(0, 110, 20, 0, houses, [tank, power, cy, gi, gi2]))
    assert cmd.log[-1] == (0x10, 0x31)

    # Among threats, the most value killed per frame: a Rhino takes on a full-health Grizzly
    # rather than a full-health GI, whose raw health is lower (the live loss vs medium).
    gi3 = ObjectState(0x32, "infantry", "E1", 1, 125, 4, 0, True, False)
    grizzly = ObjectState(0x33, "vehicle", "MTNK", 1, 300, 6, 0, True, False)
    a = Assault(db, cmd, enemy=1, gather_distance=0)
    a.tick(Snapshot(0, 100, 20, 0, houses, [tank, cy, gi3, grizzly]))
    assert cmd.log[-1] == (0x10, 0x33)


def test_assault_gathers_before_attacking():
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.assault import Assault
    from ra2tools.gamedata import GameData
    from ra2tools.memory import HouseState, ObjectState, Snapshot

    db = GameData.load(data)

    class Cmd:
        def __init__(self):
            self.log = []

        def attack(self, unit, target):
            self.log.append(("attack", unit, target))

        def move(self, unit, cell):
            self.log.append(("move", unit, cell))

    houses = [HouseState(1, 0, "Russians", True, True, False, 0, 0, 0),
              HouseState(2, 1, "Americans", False, False, False, 0, 0, 0)]
    cy = ObjectState(0x20, "building", "GACNST", 1, 100, 100, 0, True, False)
    fast = ObjectState(0x10, "vehicle", "HTNK", 0, 100, 0, 0, True, False)
    slow = ObjectState(0x11, "vehicle", "APOC", 0, 100, 0, 0, True, False)
    cmd = Cmd()
    a = Assault(db, cmd, enemy=1)
    a.tick(Snapshot(0, 0, 20, 0, houses, [fast, slow, cy]))
    assert a.rally == (78, 0)
    assert sorted(cmd.log) == [("move", 0x10, (78, 0)), ("move", 0x11, (78, 0))]
    # The Rhino is there, the Apocalypse (most of the value) is not: still waiting.
    fast_there = ObjectState(0x10, "vehicle", "HTNK", 0, 100, 78, 0, True, False)
    a.tick(Snapshot(0, 400, 20, 0, houses, [fast_there, slow, cy]))
    assert a.attack_started is None and not any(e[0] == "attack" for e in cmd.log)
    slow_there = ObjectState(0x11, "vehicle", "APOC", 0, 100, 76, 1, True, False)
    a.tick(Snapshot(0, 800, 20, 0, houses, [fast_there, slow_there, cy]))
    # Gathered: on to the second rally 14 cells out, again together.
    assert a.attack_started is None and a.rally == (86, 0)
    assert ("move", 0x10, (86, 0)) in cmd.log and ("move", 0x11, (86, 0)) in cmd.log
    fast_close = ObjectState(0x10, "vehicle", "HTNK", 0, 100, 86, 0, True, False)
    a.tick(Snapshot(0, 1000, 20, 0, houses, [fast_close, slow_there, cy]))
    assert a.attack_started is None  # the tank waits for the infantry
    slow_close = ObjectState(0x11, "vehicle", "APOC", 0, 100, 85, 1, True, False)
    a.tick(Snapshot(0, 1200, 20, 0, houses, [fast_close, slow_close, cy]))
    assert a.attack_started == 1200
    assert ("attack", 0x10, 0x20) in cmd.log and ("attack", 0x11, 0x20) in cmd.log


def test_victory_problem_one_objective_per_difficulty():
    """Easy and hard opponents: each gets a victory-frame objective; beating only the easy one is
    allowed (a loss scores LOSS_FRAMES+), losing to both is a violation; preference picks by difficulty."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner, choose_victory, victory_objectives
    from ra2tools.optimize import ParetoResult
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    easy = Opponent("e", "Americans", "Russians", [0], [{}], [{"GACNST": 1}], 40.0, difficulty="easy")
    hard = Opponent("h", "Americans", "Russians", [0], [{"MTNK": 8}], [{"GACNST": 1, "ATESLA": 2}], 40.0,
                    difficulty="hard")
    p = VictoryProblem(db, "Russians", [], ["HTNK"], cal, planner=PolicyPlanner(db, "Russians", cal),
                       opponents=[easy, hard])
    assert victory_objectives(p) == ["easy", "hard", "spent", "survive"]
    small, big, none = p.evaluate((2,)), p.evaluate((20,)), p.evaluate((0,))
    assert len(small.objectives) == 4 and small.feasible
    assert small.info["vs"]["e"]["won"] and not small.info["vs"]["h"]["won"]
    assert small.objectives[1] >= VictoryProblem.LOSS_FRAMES
    assert big.info["vs"]["h"]["won"] and big.objectives[1] < VictoryProblem.LOSS_FRAMES
    res = ParetoResult([(2,), (20,)], [small, big], [])
    assert choose_victory(p, res, {"easy": 1}) == 0 and choose_victory(p, res, {"hard": 1}) == 1


def test_ai_base_reinforces_rebuilds_and_counterattacks():
    """The AI keeps playing during a VictoryProblem fight: units appear only while their factory
    stands, lost buildings come back while the construction yard stands, and an attack wave that
    comes before we arrive is fought first."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.combat import Battle
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    tanks = [(50, "MTNK")] * 6
    plain = Battle(db, {"HTNK": 4}, {"GACNST": 1}, cal, 12, 1.5).run(5, 20000)
    no_factory = Battle(db, {"HTNK": 4}, {"GACNST": 1}, cal, 12, 1.5, spawns=tanks).run(5, 20000)
    factory = Battle(db, {"HTNK": 4}, {"GACNST": 1, "GAWEAP": 1}, cal, 12, 1.5, spawns=tanks).run(5, 20000)
    assert no_factory.frames == plain.frames and no_factory.winner == "A"
    assert factory.frames > plain.frames and factory.a_value_left < plain.a_value_left  # six Grizzlies join

    base = {"GACNST": 1, "GAPOWR": 4}
    once = Battle(db, {"E2": 4}, base, cal, 12, 1.5).run(5, 20000)
    again = Battle(db, {"E2": 4}, base, cal, 12, 1.5, rebuild=True).run(5, 20000)
    assert again.frames > once.frames or again.b_value_left > once.b_value_left

    wave = [{"frame": 100, "units": {"MTNK": 30}}]
    opp = Opponent("w", "Americans", "Russians", [0], [{}], [{"GACNST": 1}], 40.0, attacks=wave)

    def problem(**kw):
        return VictoryProblem(db, "Russians", [], ["HTNK"], cal, planner=PolicyPlanner(db, "Russians", cal),
                              opponent=opp, **kw)

    hit = problem().fight({"HTNK": 5}, 0.0, opp)
    assert not hit["won"] and hit["lost_to_wave"] == 100
    assert problem(counterattack=False).fight({"HTNK": 5}, 0.0, opp)["won"]


def test_wave_fight_delays_departure_either_way():
    """Being ready just before or just after an AI wave must not change when we can set off:
    either way the wave has to be beaten first (a bug once let a later army skip that time)."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    opp = Opponent("w", "Americans", "Russians", [0], [{}], [{"GACNST": 1}], 40.0,
                   attacks=[{"frame": 1000, "units": {"E1": 6}}])
    p = VictoryProblem(db, "Russians", [], ["HTNK"], cal, planner=PolicyPlanner(db, "Russians", cal), opponent=opp)
    before, after = p.fight({"HTNK": 3}, 900.0, opp), p.fight({"HTNK": 3}, 1100.0, opp)
    assert before["won"] and after["won"] and before["waves_met"] == after["waves_met"] == 6
    assert before["arrive_frame"] <= after["arrive_frame"]
    assert before["arrive_frame"] > 1000 + p.march_frames({"HTNK": 3}, opp)


def test_defence_fights_only_raiders_near_our_base():
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.assault import Defence
    from ra2tools.gamedata import GameData
    from ra2tools.memory import HouseState, ObjectState, Snapshot

    db = GameData.load(data)

    class Cmd:
        def __init__(self):
            self.log = []

        def attack(self, unit, target):
            self.log.append((unit, target))

    houses = [HouseState(1, 0, "Russians", True, True, False, 0, 0, 0),
              HouseState(2, 1, "Americans", False, False, False, 0, 0, 0)]
    yard = ObjectState(0x01, "building", "NACNST", 0, 100, 0, 0, True, False)
    tank = ObjectState(0x10, "vehicle", "HTNK", 0, 100, 2, 0, True, False)
    far = ObjectState(0x30, "vehicle", "MTNK", 1, 100, 60, 0, True, False)
    raider = ObjectState(0x31, "vehicle", "MTNK", 1, 100, 15, 0, True, False)
    hurt = ObjectState(0x32, "infantry", "E1", 1, 20, 6, 0, True, False)
    cmd = Cmd()
    d = Defence(db, cmd, enemy=1, yard_guard=0)
    d.tick(Snapshot(0, 100, 20, 0, houses, [yard, tank, far]))
    assert cmd.log == []  # nothing near the base: stay put
    d.tick(Snapshot(0, 200, 20, 0, houses, [yard, tank, far, raider]))
    assert cmd.log == [(0x10, 0x31)]
    d.tick(Snapshot(0, 300, 20, 0, houses, [yard, tank, far, raider, hurt]))
    assert cmd.log == [(0x10, 0x31)]  # a Rhino keeps the Grizzly: more value per shot than a GI



def test_defence_guards_the_yard_against_tanya_drop():
    """Live vs hard (2026-11-02): a Nighthawk lured the army away and Tanya blew up the yard."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.assault import Defence
    from ra2tools.gamedata import GameData
    from ra2tools.memory import HouseState, ObjectState, Snapshot

    db = GameData.load(data)

    class Cmd:
        def __init__(self):
            self.attacks, self.moves = [], []

        def attack(self, unit, target):
            self.attacks.append((unit, target))

        def move(self, unit, cell):
            self.moves.append((unit, cell))

    houses = [HouseState(1, 0, "Russians", True, True, False, 0, 0, 0),
              HouseState(2, 1, "Americans", False, False, False, 0, 0, 0)]
    yard = ObjectState(0x01, "building", "NACNST", 0, 1000, 0, 0, True, False)
    tank = ObjectState(0x10, "vehicle", "HTNK", 0, 400, 3, 0, True, False)
    flak = ObjectState(0x11, "vehicle", "HTK", 0, 300, 3, 1, True, False)
    e2 = ObjectState(0x12, "infantry", "E2", 0, 125, 1, 1, True, False)
    heli = ObjectState(0x30, "vehicle", "SHAD", 1, 200, 12, 0, True, False)
    cmd = Cmd()
    d = Defence(db, cmd, enemy=1, yard_guard=1)
    d.tick(Snapshot(0, 100, 20, 0, houses, [yard, tank, flak, e2, heli]))
    assert (0x11, 0x30) in cmd.attacks          # the flak track can hit the Nighthawk
    assert all(u != 0x10 for u, _ in cmd.attacks)  # the Rhino cannot: it does not chase it
    assert all(u != 0x12 for u, _ in cmd.attacks)  # the yard guard (nearest the yard) stays
    tanya = ObjectState(0x31, "infantry", "TANY", 1, 125, 2, 2, True, False)
    gi = ObjectState(0x32, "infantry", "E1", 1, 125, 8, 0, True, False)
    d.tick(Snapshot(0, 200, 20, 0, houses, [yard, tank, flak, e2, heli, tanya, gi]))
    assert (0x12, 0x31) in cmd.attacks and (0x10, 0x31) in cmd.attacks  # Tanya first
    far = ObjectState(0x10, "vehicle", "HTNK", 0, 400, 30, 0, True, False)
    d.tick(Snapshot(0, 600, 20, 0, houses, [yard, far, flak, e2]))
    assert any(u == 0x10 for u, _ in cmd.moves)  # nothing to fight: the stray walks home


def test_ai_answers_aircraft_with_anti_air():
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    opp = Opponent("r", "Americans", "Russians", [0], [{}], [{"GACNST": 1}], 40.0,
                   responses={"air": {"delay": 100, "every": 1000, "max": 2, "add": {"NASAM": 1}}})
    assert opp.response("air", 1000, 1050) == {} and opp.response("air", 1000, 1100) == {"NASAM": 1}
    assert opp.response("air", 1000, 9000) == {"NASAM": 2}
    p = VictoryProblem(db, "Russians", [], ["ZEP", "HTNK"], cal, planner=PolicyPlanner(db, "Russians", cal),
                       opponent=opp)
    assert p.fight({"ZEP": 1}, 5000.0, opp)["enemy"].get("NASAM", 0) >= 1
    assert "NASAM" not in p.fight({"HTNK": 3}, 5000.0, opp)["enemy"]



def test_saboteur_raid_needs_defenders_at_home():
    """Tanya's drop (live vs hard, 2026-11-02): only who is home and built by then defends."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    opp = Opponent("r", "Americans", "Russians", [0], [{}], [{"GACNST": 1}], 40.0,
                   raids=[{"frame": 3000, "units": {"TANY": 1, "E1": 6}}])
    p = VictoryProblem(db, "Russians", [], ["HTNK", "E2"], cal, planner=PolicyPlanner(db, "Russians", cal),
                       opponent=opp)
    big = {"HTNK": 6, "E2": 30}
    assert p.at_home(Counter(big), Counter(), Counter(), 3000, 30000) == Counter({"E2": 3})
    assert p.fight(big, 30000.0, opp).get("lost_to_raid") == 3000  # mostly not built yet
    assert "lost_to_raid" not in p.fight(big, 2000.0, opp)       # all home and built


def test_ai_replaces_lost_units_within_its_budget():
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.combat import Battle
    from ra2tools.gamedata import GameData

    db = GameData.load(data)
    base = {"GAPILE": 1, "GAPOWR": 1, "GACNST": 1, "E1": 6}
    army = {"E2": 20}
    # Without replacement the conscripts win; with an unlimited budget the barracks keeps
    # remaking GIs, and a zero budget is the same as no replacement.
    none = Battle(db, army, base, rebuild=True, replace_rate=0).run(5, 6000)
    free = Battle(db, army, base, rebuild=True, replace_rate=None).run(5, 6000)
    assert none.winner == "A"
    assert free.b_value_left > none.b_value_left or free.winner != "A" or free.frames > none.frames


def test_home_guard_is_unused_starting_infantry():
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.problems import home_guard

    db = GameData.load(data)
    start = {"HTNK": 4, "E2": 12}
    assert home_guard(db, {"FLAKT": 4, "ZEP": 2}, start) == {"E2": 12}  # tanks still attack
    assert home_guard(db, {"E2": 3, "SHK": 10}, start) == {}  # the plan uses conscripts


def test_assault_cuts_power_under_prism_towers_then_hits_factories():
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.assault import Assault
    from ra2tools.gamedata import GameData
    from ra2tools.memory import ObjectState

    db = GameData.load(data)
    a = Assault(db, None, enemy=1, gather_distance=0)
    tank = ObjectState(0x10, "vehicle", "HTNK", 0, 100, 0, 0, True, False)
    b = lambda addr, tid, x: ObjectState(addr, "building", tid, 1, 100, x, 0, True, False)
    power, weap, airport, prism = b(1, "GAPOWR", 40), b(2, "GAWEAP", 30), b(3, "CAAIRP", 20), b(4, "ATESLA", 50)
    assert a._choose(tank, [power, weap, airport, prism]).type_id == "GAPOWR"  # prism needs power
    assert a._choose(tank, [power, weap, airport]).type_id == "CAAIRP"  # no prism: nearest maker
    assert a._choose(tank, [power, weap]).type_id == "GAWEAP"


def test_quantile_opponent_and_topk_seeds():
    """Paper (CCC 2019) parts: a p-quantile AI takes each slot from the ceil(N*p)-th game by
    threat, keeps waves most games had (early frame, strong army) and drops response-only
    buildings; victory_seeds ranks known armies in the simulator and keeps the best top_k."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner, victory_objectives, victory_seeds
    from ra2tools.opponent import Opponent, _rank, quantile
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    assert [_rank(3, p) for p in (0.0, 0.5, 0.8, 1.0)] == [0, 1, 2, 2]
    db = GameData.load(data)
    base = {"GACNST": 1}

    def game(n, wave_at, name):
        return Opponent(name, "Americans", "Russians", [0, 3000, 6000], [{}, {"MTNK": n}, {"MTNK": n}],
                        [base, {**base, "NASAM": 1}, base], 40.0, source=name, difficulty="hard",
                        attacks=[{"frame": wave_at, "units": {"MTNK": n}}] if wave_at else [])
    games = [game(2, 5000, "a"), game(6, 4000, "b"), game(10, None, "c")]
    template = Opponent("t", "Americans", "Russians", [0], [{}], [base], 50.0, difficulty="hard",
                        responses={"air": {"delay": 1, "every": 1, "add": {"NASAM": 1}}}, replace_rate=1.0)
    q50 = quantile(db, games, 0.5, "hard_q50", template, slot=3000)
    q80 = quantile(db, games, 0.8, "hard_q80", template, slot=3000)
    assert q50.at(3000)[0] == {"MTNK": 6} and q80.at(3000)[0] == {"MTNK": 10}
    assert all("NASAM" not in b for b in q50.buildings)
    # 2 of 3 games had a wave; of 2 samples the ceil(2*p)-th smallest
    assert q50.attacks == [{"frame": 4000, "units": {"MTNK": 2}}]
    assert q80.attacks == [{"frame": 4000, "units": {"MTNK": 6}}]
    assert q50.base_distance == 50.0 and q50.replace_rate == 1.0 and q50.notes["quantile"]["p"] == 0.5

    cal = Calibration()
    p = VictoryProblem(db, "Russians", [], ["HTNK", "E2"], cal, planner=PolicyPlanner(db, "Russians", cal),
                       opponents=[q50, q80], counterattack=False)  # ranking only: no waves mid-build
    assert victory_objectives(p) == ["hard_q50", "hard_q80", "spent", "survive"]
    seeds = victory_seeds(p, [{"E2": 1}, {"HTNK": 12}, {"HTNK": 12}, {"XX": 3}], top_k=2)
    assert seeds == [(0, 1), (12, 0)]  # the soonest win first, duplicates and unknown types dropped


def test_early_defence_genes_are_built_first():
    """VictoryProblem.early: extra genes built before the army (towers stay home as guard)."""
    from pathlib import Path

    import pytest

    data = Path(__file__).parent.parent / "data"
    if not (data / "rulesmd.ini").exists():
        pytest.skip("run extract.py first")
    from ra2tools.gamedata import GameData
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.params import Calibration
    from ra2tools.problems import VictoryProblem

    db = GameData.load(data)
    cal = Calibration()
    opp = Opponent("r", "Americans", "Russians", [0], [{}], [{"GACNST": 1}], 40.0)
    p = VictoryProblem(db, "Russians", [], ["HTNK"], cal, planner=PolicyPlanner(db, "Russians", cal),
                       opponent=opp, early=["E2", "NALASR"], max_early=3)
    g = p.genome_of({"HTNK": 2}, {"E2": 5, "NALASR": 2})
    assert g == (2, 3, 2) and p.army(g) == {"HTNK": 2} and p.early_of(g) == {"E2": 3, "NALASR": 2}
    ev = p.evaluate(g)
    assert ev.info["army"].get("E2", 0) >= 3            # early units march with the army
    assert ev.info["guard"].get("NALASR") == 2          # early towers stay home
    done = [(f, t) for f, k, t in p.last_events if k == "done"]
    first_tank = min(f for f, t in done if t == "HTNK")
    assert all(f < first_tank for f, t in done if t == "NALASR")  # built before the army
