"""Play a build task live with the trained policy: every empty queue asks the model on the spot.

Same job tracking, placement and MCV deploy as BuildExecutor, but instead of a fixed sequence each
idle production queue builds an EconomyState from the memory snapshot (state_from_snapshot), masks
the legal items with the same rules as BuildEnv and lets the policy choose. A WAIT holds that queue
until something finishes, as in training. Frames are counted from the MCV deploy order, like the
simulation's frame 0. Units the skirmish starts with (tanks, infantry) are left out of the
model's state and the target count, since every simulated game starts with just the MCV.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from .bridge import BridgeError
from .env import MCVS, WAIT, BuildEnv, Observation, state_from_snapshot
from .executor import CATEGORIES, BuildExecutor, _Job
from .memory import Snapshot


@dataclass
class PolicyExecutor(BuildExecutor):
    env: BuildEnv | None = None
    policy: object = None          # BuildPolicy (anything with act(env, obs, weights))
    weights: dict = field(default_factory=lambda: {"frames": 1.0, "spent": 0.0})
    decisions: list[dict] = field(default_factory=list)  # every model call: frame, queue, choice, ms
    spent: float = 0.0
    _snap: Snapshot | None = None
    _waiting: set = field(default_factory=set)
    army: Counter = field(default_factory=Counter)  # starting units, not ours to count
    # Targets after the current ones (execute.py --early): the early defence first, then all.
    then: dict | None = None

    @property
    def targets(self) -> dict[str, int]:
        return self.env.targets

    @property
    def done(self) -> bool:
        if self._snap is None or self.deploy_frame is None:
            return False
        # Count what we finished, not what still stands: units lost defending the base are not
        # rebuilt before the attack (live 2026-11-03: replacing dead HTKs held it back 10000+ frames).
        built = Counter(t for _, k, t in self.events if k == "done")
        if all(built[t] >= n for t, n in self.targets.items()):
            if self.then is None:
                return True
            self.env.targets, self.then = dict(self.then), None
            self._waiting.clear()
        return False

    def tick(self, snap: Snapshot) -> None:
        self._snap = snap
        if self.deploy_frame is None:
            self.army = Counter({t: n for t, n in self.owned(snap).items()
                                 if t not in MCVS and self.db.technos.get(t) is not None
                                 and self.db.technos[t].kind != "building"})
        before = sum(1 for e in self.events if e[1] == "done")
        super().tick(snap)
        if sum(1 for e in self.events if e[1] == "done") != before:
            self._waiting.clear()  # something finished: prerequisites may have changed

    def _state(self, snap: Snapshot):
        state = state_from_snapshot(self.env.sim, snap)
        state.owned -= self.army
        offset = self.deploy_frame or 0
        state.frame = float(snap.frame - offset)
        state.spent = self.spent
        state.events = [(f - offset, k, t) for f, k, t in self.events if k in ("start", "done")]
        return state

    def _start_next(self, cat: str, frame: int, owned: dict[str, int]) -> None:
        snap = self._snap
        if snap is None or cat in self._waiting or self.done:
            return
        state = self._state(snap)
        mask = self.env.legal(state, cat)
        if not mask[1:].any():
            return
        t0 = time.perf_counter()
        a = self.policy.act(self.env, Observation(state, cat, mask, self.targets), self.weights)
        ms = (time.perf_counter() - t0) * 1000
        type_id = None if a == WAIT else self.env.vocab[a - 1]
        # The model's inputs in readable form, to trace live decisions that differ from the sim.
        self.decisions.append({"frame": frame, "queue": cat, "choice": type_id or "WAIT", "ms": ms,
                               "money": state.money, "owned": dict(state.owned),
                               "jobs": {c: [j.techno.id, round(j.progress, 3)] for c, j in state.jobs.items() if j},
                               "harvesters": len(state.harvesters), "docks": len(state.dock_free)})
        if type_id is None:
            self._waiting.add(cat)
            return
        try:
            self.commander.produce(type_id)
        except BridgeError as e:
            if "cannot build" in str(e):
                return  # the game disagrees (e.g. still placing); ask again next tick
            raise
        self.spent += self.db.technos[type_id].cost
        self._jobs[cat] = _Job(type_id, frame, owned.get(type_id, 0))
        self.events.append((frame, "start", type_id))


HARVESTERS_FIRST = 3  # what the policy owns anyway by mid-game; without them stage 1 starves the economy


def first_stage(db, country: str, early: dict[str, int]) -> dict[str, int]:
    """Targets of the early-defence stage: the defence plus the harvesters. Asked for 8 flak
    tracks alone, the policy built them with one harvester and the army came 6500 frames later;
    with 3 harvesters only ~1500 later (sim, 2026-11-02)."""
    harv = db.basics(country)["harvester"]
    return {**early, harv: max(early.get(harv, 0), HARVESTERS_FIRST)}


def simulate(env: BuildEnv, policy, weights: dict, early: dict | None = None) -> tuple[list, float, float, bool]:
    """The same policy in the simulator from the start: (events, finish frame, spent, finished).
    `early` is built first, as PolicyExecutor.then does live."""
    state = None
    if early:
        first = BuildEnv(env.db, env.country, first_stage(env.db, env.country, early), env.cal, vocab=env.vocab)
        obs = first.reset()
        while obs is not None:
            obs = first.step(policy.act(first, obs, weights))
        state = first.state
    obs = env.reset(state)
    while obs is not None:
        obs = env.step(policy.act(env, obs, weights))
    s = env.state
    return list(s.events), s.frame, s.spent, env.finished(s)


__all__ = ["PolicyExecutor", "simulate", "first_stage", "CATEGORIES"]
