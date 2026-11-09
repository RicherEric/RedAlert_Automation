"""BuildExecutor against a toy game that follows the sidebar rules the real one enforces."""
from pathlib import Path

import pytest

from ra2tools.bridge import BridgeError
from ra2tools.executor import BuildExecutor, compare
from ra2tools.gamedata import GameData
from ra2tools.memory import FactoryState, HouseState, ObjectState, Snapshot

DATA = Path(__file__).parent.parent / "data"
pytestmark = pytest.mark.skipif(not (DATA / "rulesmd.ini").exists(), reason="run extract.py first")

BUILD_TICKS = 3


@pytest.fixture(scope="module")
def db():
    return GameData.load(DATA)


class ToyGame:
    """Each tick is 10 frames; production takes BUILD_TICKS ticks; buildings need PLACE."""

    def __init__(self, db):
        self.db = db
        self.frame = 0
        self.objects = [("SMCV", True)]
        self.factories = {}  # category -> [type, ticks]
        self.log = []
        self.no_room = set()  # buildings the game will not let down anywhere
        self.taken = set()    # cells where nothing can go down

    def snapshot(self):
        house = HouseState(1, 0, "Russians", True, True, False, 10000, 0, 0)
        objs = [ObjectState(0x1000 + i, "", t, 0, 100, 0, 0, on_map, False)
                for i, (t, on_map) in enumerate(self.objects)]
        facs = [FactoryState(c, t, min(n, BUILD_TICKS) / BUILD_TICKS, False)
                for c, (t, n) in self.factories.items()]
        return Snapshot(0.0, self.frame, 30, 0, [house], objs, facs)

    def step(self):
        self.frame += 10
        for cat, job in list(self.factories.items()):
            job[1] += 1
            if job[1] >= BUILD_TICKS and cat != "building":
                self.objects.append((job[0], True))
                del self.factories[cat]

    def owned(self, tid):
        return any(t == tid and m for t, m in self.objects)

    # Commander
    def deploy(self, unit):
        self.log.append("DEPLOY")
        self.objects = [o for o in self.objects if o[0] != "SMCV"] + [("NACNST", True)]
        return "deploy"

    def produce(self, tid):
        t = self.db.technos[tid]
        cat = t.kind if t.kind in ("building", "infantry", "aircraft") else "vehicle"
        factory = {"vehicle": "NAWEAP", "infantry": "NAHAND", "building": "NACNST"}[cat]
        needs_proc = tid == "NAWEAP"
        if not self.owned(factory) or cat in self.factories or (needs_proc and not self.owned("NAREFN")):
            raise BridgeError(f"cannot build {tid} now (CanBuild=0)")
        self.factories[cat] = [tid, 0]
        self.log.append(f"PRODUCE {tid}")
        return f"produce {tid}"

    def place(self, tid, cell=None):
        job = self.factories.get("building")
        if not job or job[0] != tid or job[1] < BUILD_TICKS:
            raise BridgeError(f"{tid} is not finished yet")
        if cell is not None and cell in self.taken:
            raise BridgeError(f"cannot place {tid} at {cell[0]} {cell[1]}")
        self.log.append(f"PLACE {tid}" + (f" {cell[0]} {cell[1]}" if cell else ""))
        if tid in self.no_room:
            return (10, 10)  # the order is sent, the building never lands
        del self.factories["building"]
        self.objects.append((tid, True))
        if tid == "NAREFN":
            self.objects.append(("HARV", True))  # free harvester
        return (10, 10)

    def abandon(self, tid):
        if self.factories.get("building", [None])[0] == tid:
            del self.factories["building"]
        self.log.append(f"ABANDON {tid}")
        return f"abandon {tid}"


def run(db, seq, ticks=100):
    game = ToyGame(db)
    ex = BuildExecutor(db, seq, game)
    for _ in range(ticks):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    return game, ex


def test_runs_order_in_category_queues(db):
    game, ex = run(db, ["NAPOWR", "NAREFN", "NAWEAP", "HTNK", "HTNK"])
    assert ex.done
    assert game.log[0] == "DEPLOY" and ex.deploy_frame == 0
    builds = [l for l in game.log if l.startswith(("PRODUCE NA", "PLACE"))]
    assert builds == ["PRODUCE NAPOWR", "PLACE NAPOWR", "PRODUCE NAREFN", "PLACE NAREFN",
                      "PRODUCE NAWEAP", "PLACE NAWEAP"]
    assert [t for _, k, t in ex.events if k == "done"] == ["NAPOWR", "NAREFN", "NAWEAP", "HTNK", "HTNK"]
    assert sum(t == "HTNK" for t, _ in game.objects) == 2


def test_free_harvester_is_not_the_ordered_one(db):
    # HARV is ordered first in the vehicle queue but can only start once the factory exists;
    # the refinery's free harvester appears earlier and must not complete the job.
    game, ex = run(db, ["NAPOWR", "NAREFN", "NAWEAP", "HARV"])
    assert ex.done
    assert sum(t == "HARV" for t, _ in game.objects) == 2
    done = {t: f for f, k, t in ex.events if k == "done"}
    assert done["HARV"] > done["NAWEAP"]


def test_compare_pairs_nth_occurrence():
    predicted = [(0, "start", "A"), (100, "done", "A"), (100, "start", "A"), (200, "done", "A")]
    actual = [(50, "start", "A"), (160, "done", "A"), (160, "start", "A"), (240, "done", "A")]
    rows = compare(predicted, actual, offset=50)
    assert [(r["n"], r["done"], r["diff"]) for r in rows] == [(1, 110, 10), (2, 190, -10)]


def test_policy_executor_asks_model_per_queue(db):
    """Live policy loop: each idle queue asks the policy; WAIT holds until something finishes."""
    from ra2tools.env import WAIT, BuildEnv, needed_items
    from ra2tools.live_policy import PolicyExecutor
    from ra2tools.params import Calibration

    class PathPolicy:  # stand-in for BuildPolicy: first legal item on the build path, else WAIT
        def act(self, env, obs, weights):
            need = needed_items(env, obs)
            legal = [i for i in range(1, env.n_actions) if obs.mask[i] and env.vocab[i - 1] in need]
            return legal[0] if legal else WAIT

    game = ToyGame(db)
    env = BuildEnv(db, "Russians", {"HTNK": 1}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], game, env=env, policy=PathPolicy())
    for _ in range(100):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done
    produced = [l.split()[1] for l in game.log if l.startswith("PRODUCE")]
    assert produced[-1] == "HTNK" and "NAWEAP" in produced and "NAREFN" in produced
    assert produced.index("NAREFN") < produced.index("NAWEAP")
    assert all(d["choice"] != "WAIT" or d["queue"] != "building" for d in ex.decisions[:1])
    assert ex.spent == sum(db.technos[t].cost for t in produced)


def test_policy_executor_wait_ends_when_nothing_builds(db):
    """Live 2026-11-06: every queue chose WAIT (short of money) with nothing in production, so
    nothing could finish to end the wait and the build stalled for 20000 frames."""
    from ra2tools.env import WAIT, BuildEnv, needed_items
    from ra2tools.live_policy import PolicyExecutor
    from ra2tools.params import Calibration

    class Hesitant:  # waits on its first few calls, then follows the build path
        calls = 0

        def act(self, env, obs, weights):
            self.calls += 1
            if self.calls <= 3:
                return WAIT
            need = needed_items(env, obs)
            legal = [i for i in range(1, env.n_actions) if obs.mask[i] and env.vocab[i - 1] in need]
            return legal[0] if legal else WAIT

    game = ToyGame(db)
    env = BuildEnv(db, "Russians", {"HTNK": 1}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], game, env=env, policy=Hesitant())
    for _ in range(200):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done


def test_policy_executor_stage_counts_free_harvester(db):
    """Live 2026-11-06: stage 1 asked for harvesters, the refinery's free one had no done event,
    so the stage never ended although the model saw enough and stopped building them."""
    from ra2tools.env import WAIT, BuildEnv, needed_items
    from ra2tools.live_policy import PolicyExecutor
    from ra2tools.params import Calibration

    class PathPolicy:
        def act(self, env, obs, weights):
            need = needed_items(env, obs)
            legal = [i for i in range(1, env.n_actions) if obs.mask[i] and env.vocab[i - 1] in need]
            return legal[0] if legal else WAIT

    game = ToyGame(db)
    env = BuildEnv(db, "Russians", {"HARV": 2}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], game, env=env, policy=PathPolicy(), then={"HTNK": 1})
    for _ in range(300):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done
    assert [l.split()[1] for l in game.log if l.startswith("PRODUCE")].count("HARV") == 1


def test_policy_executor_ignores_starting_army(db):
    """Skirmish starting units are not ours: the target still needs a newly built tank, and the
    model never sees the starting tanks (simulated games start with just the MCV)."""
    from ra2tools.env import WAIT, BuildEnv, needed_items
    from ra2tools.live_policy import PolicyExecutor
    from ra2tools.params import Calibration

    class PathPolicy:
        def act(self, env, obs, weights):
            need = needed_items(env, obs)
            legal = [i for i in range(1, env.n_actions) if obs.mask[i] and env.vocab[i - 1] in need]
            return legal[0] if legal else WAIT

    game = ToyGame(db)
    game.objects += [("HTNK", True), ("HTNK", True), ("E2", True)]
    env = BuildEnv(db, "Russians", {"HTNK": 1}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], game, env=env, policy=PathPolicy())
    for _ in range(100):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done and ex.army == {"HTNK": 2, "E2": 1}
    assert [l.split()[1] for l in game.log if l.startswith("PRODUCE")][-1] == "HTNK"
    assert all("HTNK" not in d["owned"] and "E2" not in d["owned"] for d in ex.decisions)


def test_job_done_when_factory_moves_on_despite_losses(db):
    """Live 2026-11-02: an HTK died while the next HTK was in production, the count never rose
    above the baseline and the vehicle queue stalled for the rest of the game."""
    game = ToyGame(db)
    game.objects += [("NACNST", True), ("NAPOWR", True), ("NAREFN", True), ("NAWEAP", True), ("HTK", True)]
    ex = BuildExecutor(db, ["HTK", "HTK"], game)
    ex.tick(game.snapshot())  # deploy check
    for _ in range(BUILD_TICKS - 1):
        ex.tick(game.snapshot())
        game.step()
    game.objects.remove(("HTK", True))  # one dies in the fight while ours is still building
    for _ in range(20):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert [t for _, k, t in ex.events if k == "done"] == ["HTK", "HTK"]
    assert ex.done


def test_building_order_the_game_dropped_is_ordered_again(db):
    # Live 2026-11-06: PRODUCE NAPOWR was accepted, nothing ever showed in the factory, and the
    # building queue waited on it all game.
    game = ToyGame(db)
    real = game.produce
    dropped = []

    def produce(tid):
        out = real(tid)
        if tid == "NAPOWR" and not dropped:
            dropped.append(tid)
            del game.factories["building"]  # accepted, then silently gone
        return out
    game.produce = produce
    ex = BuildExecutor(db, ["NAPOWR", "NAREFN"], game, lost_after=50)
    for _ in range(100):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done and dropped
    assert [k for _, k, t in ex.events if t == "NAPOWR"] == ["start", "lost", "start", "done"]


def test_policy_executor_counts_garrisoned_infantry(db):
    from collections import Counter
    from ra2tools.env import BuildEnv
    from ra2tools.live_policy import PolicyExecutor
    from ra2tools.params import Calibration
    env = BuildEnv(db, "Russians", {"E2": 1}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], ToyGame(db), env=env, army=Counter({"E2": 3}), hidden=Counter({"E2": 2}))
    game = ToyGame(db)
    game.objects = [("NACNST", True), ("NAHAND", True), ("E2", True), ("E2", False)]
    ex.deploy_frame = 0
    state = ex._state(game.snapshot())
    assert state.owned["E2"] == 0  # 1 on the map + 2 inside - the 3 we started with


def test_defence_placed_from_the_defence_queue(db):
    # The game builds BuildCat=Combat buildings in a separate factory (Primary_ForDefenses).
    game = ToyGame(db)
    snapshot = game.snapshot

    def snap():
        s = snapshot()
        s.factories = [FactoryState("defense", f.type_id, f.progress, False) if f.type_id == "NAFLAK" else f
                       for f in s.factories]
        return s
    game.snapshot = snap
    game.objects = [("NACNST", True), ("NAHAND", True)]
    ex = BuildExecutor(db, ["NAFLAK"], game, deploy_frame=0)
    for _ in range(30):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done and "PLACE NAFLAK" in game.log
    assert not [e for e in ex.events if e[1] == "lost"]


def test_policy_stops_ordering_a_unit_that_never_arrives(db):
    # Live 2026-11-06 every conscript the barracks finished was deleted before reaching the map,
    # and the policy, still short of one, paid for 70 in a row.
    from ra2tools.env import BuildEnv
    from ra2tools.live_policy import VANISHED, PolicyExecutor
    from ra2tools.params import Calibration

    class Wants:
        def act(self, env, obs, weights):
            i = env.vocab.index("E2") + 1
            return i if obs.mask[i] else 0

    game = ToyGame(db)
    step = game.step

    def lose_conscripts():
        step()
        game.objects = [o for o in game.objects if o[0] != "E2"]
    game.step = lose_conscripts
    game.objects = [("NACNST", True), ("NAHAND", True)]
    env = BuildEnv(db, "Russians", {"E2": 10}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], game, env=env, policy=Wants())
    ex.deploy_frame = 0
    for _ in range(200):
        ex.tick(game.snapshot())
        game.step()
    assert game.log.count("PRODUCE E2") == VANISHED


def test_unplaceable_building_is_cancelled(db):
    """Live 2026-11-08: a finished refinery never went down; the building queue sat on it and
    every other queue's WAIT with it. Retry the placement, then cancel and build the rest."""
    game = ToyGame(db)
    game.no_room = {"NALASR"}
    ex = BuildExecutor(db, ["NAPOWR", "NAREFN", "NALASR", "NAWEAP", "HTNK"], game)
    ex.place_every = 10
    for _ in range(200):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done
    assert game.log.count("PLACE NALASR") == ex.place_tries and "ABANDON NALASR" in game.log
    assert ("NAWEAP", True) in game.objects and ("HTNK", True) in game.objects
    assert [t for _, k, t in ex.events if k == "cancel"] == ["NALASR"]


def test_policy_executor_skips_cancelled_and_extra_factories(db):
    from ra2tools.env import BuildEnv
    from ra2tools.live_policy import PolicyExecutor
    from ra2tools.params import Calibration

    class Greedy:  # always the first legal item: would build NAWEAP forever
        def act(self, env, obs, weights):
            return next(i for i in range(1, env.n_actions) if obs.mask[i])

    game = ToyGame(db)
    game.objects = [("NACNST", True), ("NAPOWR", True), ("NAREFN", True)] + [("NAWEAP", True)] * 3
    env = BuildEnv(db, "Russians", {"HTNK": 1}, Calibration().as_bot())
    ex = PolicyExecutor(db, [], game, env=env, policy=Greedy())
    ex.deploy_frame = 0
    ex.cancelled["NAPOWR"] = 0
    snap = game.snapshot()
    ex._snap = snap
    ex._start_next("building", 10, ex.owned(snap))
    choice = ex.decisions[-1]["choice"]
    assert choice not in ("NAWEAP", "NAPOWR")


def test_spread_placement_tries_cells_in_order(db):
    """placement.Spreader's cells come first; a taken cell moves on to the next, and with none
    left the bridge's nearest cell (live 2026-11-09: one storm razed five packed buildings)."""
    game = ToyGame(db)
    game.taken = {(1, 1)}
    ex = BuildExecutor(db, ["NAPOWR", "NAREFN"], game,
                       placer=lambda snap, tid: [(1, 1), (2, 2)] if tid == "NAPOWR" else [(1, 1)])
    for _ in range(200):
        if ex.done:
            break
        ex.tick(game.snapshot())
        game.step()
    assert ex.done
    assert "PLACE NAPOWR 2 2" in game.log and "PLACE NAREFN" in game.log


def test_spread_cells_keep_two_free_cells(db):
    from ra2tools.memory import HouseState, ObjectState, Snapshot
    from ra2tools.placement import Spreader, _gap

    sp = Spreader(db)
    assert sp.sizes["NACNST"] == (4, 4) and sp.sizes["NAPOWR"] == (3, 2)
    b = lambda addr, tid, x, y: ObjectState(addr, "building", tid, 0, 100, x + 0.5, y + 0.5, True, False)
    snap = Snapshot(0.0, 100, 15, 0, [HouseState(0, 0, "Russians", True, True, False, 10000, 0, 0)],
                    [b(1, "NACNST", 64, 19), b(2, "NAPOWR", 62, 17)])
    cells = sp(snap, "NAHAND")
    rects = [(64, 19, 4, 4), (62, 17, 3, 2)]
    assert cells and min(_gap((*cells[0], 2, 2), r) for r in rects) == 2
    assert sp(snap, "NAREFN") == [] and sp(snap, "NALASR") == []  # by the ore / facing the enemy
