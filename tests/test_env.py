"""BuildEnv must reproduce EconomySim.run when the policy follows the same sequence."""
from pathlib import Path

import numpy as np
import pytest

from ra2tools.economy import CATEGORIES, EconomySim
from ra2tools.env import HEAD, WAIT, BuildEnv, features, state_from_snapshot
from ra2tools.gamedata import GameData
from ra2tools.memory import FactoryState, HouseState, ObjectState, Snapshot

DATA = Path(__file__).parent.parent / "data"
pytestmark = pytest.mark.skipif(not (DATA / "rulesmd.ini").exists(), reason="run extract.py first")

CASES = [
    ("Russians", ["NAPOWR", "NAREFN", "NAHAND", "NAWEAP", "HARV", "NARADR", "NAPOWR", "NATECH", "APOC"], {"APOC": 2}),
    ("Americans", ["GAPOWR", "GAREFN", "GAPILE", "E1", "E1", "GAWEAP", "MTNK"], {"MTNK": 3, "ORCA": 1}),
    ("YuriCountry", ["YAPOWR", "YAREFN", "YABRCK", "INIT", "YAWEAP", "LTNK"], {"LTNK": 3}),
]


@pytest.fixture(scope="module")
def db():
    return GameData.load(DATA)


def follow(env, seq):
    """Policy: per queue, the next item of `seq`, or WAIT while it cannot start."""
    queues = {c: [t for t in seq if env.sim.category(env.db.technos[t]) == c] for c in CATEGORIES}
    heads = dict.fromkeys(CATEGORIES, 0)
    obs = env.reset()
    while obs is not None:
        q, h = queues[obs.category], heads[obs.category]
        a = env.index[q[h]] if h < len(q) else WAIT
        if a != WAIT and not obs.mask[a]:
            a = WAIT
        if a != WAIT:
            heads[obs.category] += 1
        obs = env.step(a)
    return env.state


@pytest.mark.parametrize("country,order,targets", CASES, ids=[c[0] for c in CASES])
def test_env_matches_run(db, country, order, targets):
    sim = EconomySim(db, country)
    seq = sim.resolve(order, targets)
    expected = sim.run(seq, targets, resolved=True)
    env = BuildEnv(db, country, targets)
    state = follow(env, seq)
    assert env.finished(state)
    assert int(state.frame) == expected.finish_frame
    assert state.events == expected.events
    assert state.money == pytest.approx(expected.money)


def test_masks_follow_rules(db):
    env = BuildEnv(db, "Russians", {"HTNK": 1})
    obs = env.reset()
    legal = {env.vocab[i - 1] for i in np.flatnonzero(obs.mask[1:]) + 1}
    assert obs.category == "building"
    assert "NAPOWR" in legal and "NAREFN" not in legal  # the refinery needs power first
    with pytest.raises(ValueError):
        env.step(env.index["NATECH"])
    assert features(env, obs).shape == features(env, obs, {"frames": 0.5, "spent": 0.5}).shape


def test_run_continues_from_mid_state(db):
    sim = EconomySim(db, "Russians")
    seq = sim.resolve(["HTNK", "HTNK"], {"HTNK": 2})
    full = sim.run(seq, {"HTNK": 2}, resolved=True)
    env = BuildEnv(db, "Russians", {"HTNK": 2})
    env.reset()
    # Play the first three building decisions, then hand the state to run() for the rest.
    for _ in range(3):
        env.step(env.index[seq[_]])
    rest = sim.run(seq[3:], {"HTNK": 2}, resolved=True, state=env.state)
    assert rest.finish_frame == full.finish_frame


def test_state_from_snapshot(db):
    sim = EconomySim(db, "Russians")
    house = HouseState(1, 0, "Russians", True, True, False, 4321, 200, 100)
    objs = [ObjectState(i, "", t, 0, 100, 0, 0, True, False)
            for i, t in enumerate(["NACNST", "NAPOWR", "NAREFN", "HARV", "HARV"])]
    snap = Snapshot(0.0, 2000, 20, 0, [house], objs, [FactoryState("building", "NAWEAP", 0.5, False)])
    s = state_from_snapshot(sim, snap)
    assert s.money == 4321 and s.owned["HARV"] == 2 and len(s.harvesters) == 2
    assert s.jobs["building"].techno.id == "NAWEAP" and s.jobs["building"].progress == 0.5
    assert len(s.dock_free) == 1
    res = sim.run(["HTNK"], {"HTNK": 1}, state=s)
    assert res.finish_frame is not None and res.finish_frame > 2000


def test_resolve_uses_country_specific_buildings(db):
    # Americans cannot build GAAIRC (they get AMRADR); RADAR must not fall back to a Soviet radar.
    sim = EconomySim(db, "Americans")
    assert not sim.buildable("GAAIRC") and sim.buildable("AMRADR")
    assert sim.resolve(["ORCA"], {}) == ["GAPOWR", "GAREFN", "AMRADR", "ORCA"]


def test_teacher_episode_labels_legal_actions(db):
    import random

    from ra2tools.params import Calibration
    from ra2tools.teacher import Task, episode

    task = Task("Russians", {"HTNK": 2}, {"frames": 1.0, "spent": 0.0}, 10000)
    env_vocab = BuildEnv(db, "Russians", {}).vocab
    ep = episode(db, task, Calibration(), env_vocab, random.Random(0), explore=0.0)
    assert ep["finished"]
    assert all(m[a] for m, a in zip(ep["mask"], ep["action"]))
    # Without exploration the game follows the teacher's first plan to the frame.
    assert ep["final_frame"] == ep["finish"][0]


def test_wait_always_advances_time(db):
    """Answering WAIT at the first queue of a tick must not re-run the tick (was an endless loop)."""
    env = BuildEnv(db, "Russians", {"SHK": 2})
    obs = env.reset()
    frames = []
    for _ in range(20):
        frames.append(obs.state.frame)
        obs = env.step(WAIT)
        if obs is None:
            break
    assert frames[-1] > frames[0]
    assert len(set(frames)) == len(frames)  # every WAIT moved on to a later tick


def test_sampled_tasks_respect_build_limits(db):
    import random
    from ra2tools.teacher import sample_task
    rng = random.Random(0)
    for _ in range(300):
        task = sample_task(db, rng)
        for tid, n in task.targets.items():
            limit = db.technos[tid].build_limit
            assert limit <= 0 or n <= limit, (tid, n)


def test_dagger_actor_plays_and_teacher_labels(db):
    """With an actor the actor's moves are played, but the rows keep the teacher's labels."""
    import random

    from ra2tools.params import Calibration
    from ra2tools.teacher import Task, episode

    task = Task("Russians", {"HTNK": 1}, {"frames": 1.0, "spent": 0.0}, 10000)
    vocab = BuildEnv(db, "Russians", {}).vocab
    played = []

    def stubborn(env, obs, teacher_action):  # always waits: the student mistake DAgger fixes
        played.append(teacher_action)
        return WAIT

    ep = episode(db, task, Calibration(), vocab, random.Random(0), explore=0.0, actor=stubborn,
                 max_decisions=5)
    assert len(ep["action"]) == 5 and not ep["finished"]
    assert list(ep["action"]) == played and any(a != WAIT for a in played)
    assert len(set(ep["frame"])) == 5  # waiting moved time on


def test_unstick_builds_missing_prerequisite(db):
    """A policy that only ever waits gets pushed down the targets' prerequisite chain once idle."""
    from ra2tools.policy import unstick

    env = BuildEnv(db, "Russians", {"FLAKT": 1})
    obs = env.reset()
    prefer_wait = np.zeros(env.n_actions)
    prefer_wait[WAIT] = 1.0
    assert unstick(env, obs, prefer_wait) is None  # just started: waiting is still allowed
    while obs is not None and obs.state.frame < 120:
        obs = env.step(WAIT)
    a = unstick(env, obs, prefer_wait)
    assert a is not None and env.vocab[a - 1] == "NAPOWR"  # first step of the chain to the radar


def test_features_show_build_path(db):
    """The path block lists the prerequisite chain of the remaining targets (radar for Flak Troopers)."""
    from ra2tools.env import needed_items

    env = BuildEnv(db, "Russians", {"FLAKT": 1})
    obs = env.reset()
    need = needed_items(env, obs)
    assert need["NARADR"] == 1 and need["FLAKT"] == 1 and need["NAPOWR"] >= 1
    n = len(env.vocab)
    path = features(env, obs)[HEAD + 3 * n:HEAD + 4 * n]
    assert path[env.index["NARADR"] - 1] == 1 and path[env.index["SHK"] - 1] == 0


def test_features_show_remaining_cost(db):
    """The last two features: cost of the remaining path and how far the money falls short of it."""
    from ra2tools.env import needed_items

    env = BuildEnv(db, "Russians", {"HTNK": 3})
    obs = env.reset()
    cost = sum(db.technos[t].cost * k for t, k in needed_items(env, obs).items())
    x = features(env, obs)
    assert x[-2] * 1000 == pytest.approx(cost)
    assert x[-1] * 1000 == pytest.approx(cost - obs.state.money)
