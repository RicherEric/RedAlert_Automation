"""GA teacher for imitation learning: label BuildEnv decisions with the optimiser's choice.

At each decision the teacher plans the rest of the game from the current state with the
build-order GA (BuildOrderProblem with `state`), and answers with the first item its plan puts
in the asking queue, or WAIT if that item cannot start yet. Within an episode each plan seeds the
next one, so a small GA is enough after the first decision.

`episode()` plays one game, taking the teacher's action most of the time and a random legal one
otherwise, so the data also covers states the teacher itself would never reach. With an `actor`
(DAgger), another policy chooses the moves and the teacher only labels the states it reaches.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from .economy import CATEGORIES, UnbuildableError
from .env import WAIT, BuildEnv, Observation, default_vocab, features
from .gamedata import COUNTRIES, GameData
from .optimize import run_ga
from .params import Calibration
from .problems import BuildOrderProblem

SIDE_COUNTRY = {"soviet": "Russians", "allied": "Americans", "yuri": "YuriCountry"}


def global_vocab(db: GameData, cal: Calibration) -> list[str]:
    """Union of every side's vocabulary, so one model covers all countries."""
    from .economy import EconomySim
    out: set[str] = set()
    for country in COUNTRIES:
        out.update(default_vocab(EconomySim(db, country, cal)))
    return sorted(out)


@dataclass
class Task:
    country: str
    targets: dict[str, int]
    weights: dict[str, float]
    start_credits: int


def engineer_of(db: GameData, sim) -> str | None:
    return next((t.id for t in db.technos.values() if t.engineer and sim.buildable(t.id)), None)


def sample_task(db: GameData, rng: random.Random, countries=None, cal: Calibration | None = None) -> Task:
    """Random targets: 1-2 combat unit types, and in 40% of the tasks also what the robust plans
    build first (2026-11-06): engineers for the oil derricks, extra harvesters, a defence building."""
    from .economy import EconomySim
    country = rng.choice(countries or list(SIDE_COUNTRY.values()))
    sim = EconomySim(db, country)
    combat = [t for t in db.technos.values()
              if t.kind in ("vehicle", "infantry", "aircraft") and not t.naval and t.cost > 0
              and t.combat_weapons and not t.harvester and sim.buildable(t.id)]
    targets: dict[str, int] = {}
    for t in rng.sample(combat, k=min(len(combat), rng.choice([1, 1, 2]))):
        most = max(1, min(6, 6000 // max(t.cost, 1)))
        if t.build_limit > 0:  # e.g. Yuri Prime, Tanya: only one at a time
            most = min(most, t.build_limit)
        targets[t.id] = rng.randint(1, most)
    if rng.random() < 0.4:
        eng = engineer_of(db, sim)
        derricks = len(cal.derricks) if cal else 0
        if eng and derricks and rng.random() < 0.6:
            targets[eng] = rng.randint(1, derricks)
        if rng.random() < 0.5:
            harv = db.basics(country)["harvester"]
            targets[harv] = rng.randint(2, 5)
        towers = [t for t in db.technos.values() if t.kind == "building" and t.combat_weapons and 0 < t.cost <= 1500
                  and sim.buildable(t.id)]
        if towers and rng.random() < 0.5:
            targets[rng.choice(towers).id] = rng.randint(1, 3)
    wf = rng.choice([1.0, 0.75, 0.5, 0.25])
    return Task(country, targets, {"frames": wf, "spent": 1 - wf}, rng.choice([5000, 7500, 10000, 15000]))


@dataclass
class Teacher:
    db: GameData
    task: Task
    cal: Calibration
    first_ga: dict = field(default_factory=lambda: {"pop_size": 30, "generations": 20})
    replan_ga: dict = field(default_factory=lambda: {"pop_size": 16, "generations": 8})
    seed: int = 0
    _genome: tuple | None = None

    def plan(self, env: BuildEnv) -> tuple[list[str], float, float]:
        """Best resolved sequence from env.state, with its finish frame and credits spent."""
        basics = self.db.basics(self.task.country)
        extras = [basics[k] for k in ("power", "proc", "factory", "barracks", "harvester")]
        eng = engineer_of(self.db, env.sim) if self.cal.derricks else None
        if eng:  # an engineer on a derrick pays $1000 at once: the teacher may use it for money
            extras.append(eng)
        problem = BuildOrderProblem(
            self.db, self.task.country, self.task.targets,
            extras=extras,
            cal=self.cal, max_frames=env.max_frames, state=env.state, weights=self.task.weights, max_extras=6)
        seeds = [tuple(problem.target_items)]
        if self._genome is not None:
            seeds.insert(0, self._genome)
        ga = self.replan_ga if self._genome is not None else self.first_ga
        self.seed += 1
        res = run_ga(problem, seeds=seeds, seed=self.seed, **ga)
        self._genome = res.best
        r = res.best_eval.info["result"]
        finish = r.finish_frame if r.finish_frame is not None else env.max_frames
        return res.best_eval.info["sequence"], float(finish), float(r.spent)

    def action(self, env: BuildEnv, obs: Observation) -> tuple[int, float, float]:
        """Teacher's action for the pending decision, plus the plan's finish frame and spending."""
        seq, finish, spent = self.plan(env)
        first = next((t for t in seq if env.sim.category(self.db.technos[t]) == obs.category), None)
        a = env.index.get(first, WAIT) if first else WAIT
        return (a if obs.mask[a] else WAIT), finish, spent

    def taken(self, env: BuildEnv, action: int) -> None:
        """Keep the seed genome in sync after an action was played."""
        if self._genome is None or action == WAIT:
            return
        tid = env.vocab[action - 1]
        g = list(self._genome)
        if tid in g:
            g.remove(tid)
        self._genome = tuple(g)


def episode(db: GameData, task: Task, cal: Calibration, vocab: list[str], rng: random.Random,
            explore: float = 0.15, max_frames: int = 27000,
            actor: Callable[[BuildEnv, Observation, int], int] | None = None,
            max_decisions: int = 300) -> dict[str, np.ndarray]:
    """Play one game; return per-decision arrays (features, mask, teacher action, ...).

    `actor(env, obs, teacher_action)` picks the move actually played (default: the teacher's).
    `max_decisions` stops a student stuck repeating the same mistake from flooding the data."""
    cal = replace(cal, start_credits=task.start_credits)
    env = BuildEnv(db, task.country, task.targets, cal, vocab=vocab, max_frames=max_frames)
    teacher = Teacher(db, task, cal, seed=rng.randrange(1 << 30))
    rows: dict[str, list] = {k: [] for k in ("x", "mask", "action", "finish", "spent", "frame", "category")}
    obs = env.reset()
    while obs is not None:
        try:
            a, finish, spent = teacher.action(env, obs)
        except UnbuildableError:
            break
        rows["x"].append(features(env, obs, task.weights))
        rows["mask"].append(obs.mask)
        rows["action"].append(a)
        rows["finish"].append(finish)
        rows["spent"].append(spent)
        rows["frame"].append(obs.state.frame)
        rows["category"].append(CATEGORIES.index(obs.category))
        if actor is not None:
            a = actor(env, obs, a)
        if rng.random() < explore:
            a = rng.choice(list(np.flatnonzero(obs.mask)))
        if len(rows["action"]) >= max_decisions:
            break
        teacher.taken(env, a)
        obs = env.step(int(a))
    out = {k: np.asarray(v) for k, v in rows.items()}
    out["final_frame"] = np.float32(env.state.frame)
    out["final_spent"] = np.float32(env.state.spent)
    out["finished"] = np.bool_(env.finished(env.state))
    return out
