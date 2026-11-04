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
        del self.factories["building"]
        self.objects.append((tid, True))
        if tid == "NAREFN":
            self.objects.append(("HARV", True))  # free harvester
        self.log.append(f"PLACE {tid}")
        return (10, 10)


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
