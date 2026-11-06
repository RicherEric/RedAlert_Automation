"""Step-by-step build environment on top of EconomySim, for training a build policy.

A decision is needed whenever a production queue (building / vehicle / infantry / aircraft) is
empty and at least one item for it can start. The agent answers with an index into `vocab`, or
WAIT (index 0) to leave that queue idle until the next item finishes somewhere (prerequisites
and factories only change then). Masks come from the game rules, so every chosen action is legal.

The same state type (EconomyState) can be built from a live memory snapshot with
`state_from_snapshot`, so a policy trained here sees the same features in a real game.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .economy import CATEGORIES, EconomySim, EconomyState, UnbuildableError, _Job
from .gamedata import GameData, Techno
from .params import Calibration

WAIT = 0
MCVS = {"AMCV", "SMCV", "PCV"}
FEATURE_VERSION = 4  # bump when features() changes: stored datasets and models must match
HEAD = 6  # economy values at the start of the feature vector


def default_vocab(sim: EconomySim) -> list[str]:
    """Every non-naval item the country can actually build, except MCVs and walls."""
    out = []
    for t in sim.db.technos.values():
        if (t.kind in CATEGORIES and not t.naval and t.cost > 0 and t.id not in MCVS
                and "WALL" not in t.id and "FWLL" not in t.id and sim.buildable(t.id)):
            out.append(t.id)
    return sorted(out)


@dataclass
class Observation:
    state: EconomyState
    category: str          # the queue that needs a decision
    mask: np.ndarray       # bool, len(vocab) + 1; index 0 = WAIT (always legal)
    targets: dict[str, int]


@dataclass
class BuildEnv:
    db: GameData
    country: str
    targets: dict[str, int]
    cal: Calibration = field(default_factory=Calibration)
    vocab: list[str] | None = None
    max_frames: int = 27000
    dt: int = 15

    def __post_init__(self):
        self.sim = EconomySim(self.db, self.country, self.cal, dt=self.dt)
        self.vocab = self.vocab or default_vocab(self.sim)
        self.index = {tid: i + 1 for i, tid in enumerate(self.vocab)}  # 0 is WAIT
        self.costs = np.array([self.db.technos[t].cost for t in self.vocab], dtype=float)
        self._cat_of = np.array(["wait"] + [self.sim.category(self.db.technos[t]) for t in self.vocab])
        self.state: EconomyState | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def n_actions(self) -> int:
        return len(self.vocab) + 1

    def legal(self, state: EconomyState, cat: str) -> np.ndarray:
        mask = np.zeros(self.n_actions, dtype=bool)
        mask[WAIT] = True
        if state.jobs[cat] is not None:
            return mask
        for tid, i in self.index.items():
            if self._cat_of[i] != cat:
                continue
            t = self.db.technos[tid]
            if t.build_limit > 0 and state.owned[tid] >= t.build_limit:
                continue
            if self.sim.can_start(state, t):
                mask[i] = True
        return mask

    def finished(self, state: EconomyState) -> bool:
        return all(state.owned[t] >= n for t, n in self.targets.items())

    def objectives(self, state: EconomyState) -> dict[str, float]:
        """Values to minimise; the multi-objective teacher trades these off."""
        remaining = sum(max(0, n - state.owned[t]) * self.db.technos[t].cost for t, n in self.targets.items())
        return {"frames": state.frame, "spent": state.spent, "remaining_cost": remaining}

    # ---------------------------------------------------------------- stepping
    def reset(self, state: EconomyState | None = None) -> Observation | None:
        self.state = state.copy() if state else self.sim.initial_state()
        self._waiting: set[str] = set()
        self._chosen: dict[str, Techno] = {}
        self._done_count = self._count_done()
        return self._advance()

    def step(self, action: int) -> Observation | None:
        """Answer the pending decision; returns the next one, or None when the episode ended."""
        cat = self._pending
        if action == WAIT:
            self._waiting.add(cat)
        else:
            if not self._mask[action]:
                raise ValueError(f"illegal action {action} ({self.vocab[action - 1]}) for {cat}")
            self._chosen[cat] = self.db.technos[self.vocab[action - 1]]
        return self._advance()

    @property
    def done(self) -> bool:
        return self._pending is None

    def _count_done(self) -> int:
        return sum(1 for _, kind, _ in self.state.events if kind == "done")

    def _ask(self, cat: str) -> np.ndarray | None:
        """The mask if `cat` needs a decision now, else None."""
        s = self.state
        if s.jobs[cat] is not None or cat in self._chosen or cat in self._waiting:
            return None
        mask = self.legal(s, cat)
        return mask if mask[1:].any() else None

    def _advance(self) -> Observation | None:
        """Run the simulation queue by queue until one needs a decision or the episode ends.

        Decisions are taken at the moment EconomySim.step would call pick(), so following a
        fixed sequence reproduces EconomySim.run exactly."""
        s = self.state
        chosen = self._chosen
        while True:
            if not s.ticking:
                if self.finished(s) or s.frame >= self.max_frames:
                    break
                if (not any(s.jobs.values()) and not chosen
                        and not any(self._ask(c) is not None for c in CATEGORIES)):
                    if not self._waiting:
                        break  # nothing running and nothing can start: stuck
                    # Nothing will finish to end the wait, so WAIT lasts one tick here.
                    self._waiting.clear()
                s.tick_speed = self.sim.tick_start(s)
                s.ticking = True
            while s.queue_next < len(CATEGORIES):
                cat = CATEGORIES[s.queue_next]
                mask = self._ask(cat)
                if mask is not None:
                    self._pending, self._mask = cat, mask
                    return Observation(s, cat, mask, self.targets)
                self.sim.tick_queue(s, cat, lambda c: chosen.pop(c, None))
                s.queue_next += 1
                done = self._count_done()
                if done != self._done_count:
                    self._done_count = done
                    self._waiting.clear()  # something finished: prerequisites may have changed
            if self.finished(s):
                break
            s.frame += self.dt
            s.queue_next = 0
            s.ticking = False
        self._pending = None
        return None


# ------------------------------------------------------------------------------ features
def needed_items(env: BuildEnv, obs: Observation) -> Counter:
    """What is still to be built for the remaining targets, prerequisites and power included."""
    s = obs.state
    have = s.owned + Counter(j.techno.id for j in s.jobs.values() if j)
    try:
        return Counter(env.sim.resolve([], obs.targets, have=have))
    except UnbuildableError:
        return Counter()


def features(env: BuildEnv, obs: Observation, weights: dict[str, float] | None = None) -> np.ndarray:
    """Fixed-length vector for a policy: economy, owned/in-production/target counts per vocab item,
    the shortest build path to the remaining targets (so rare prerequisite chains, such as the
    radar a Flak Trooper needs, are visible), the queue asking, and the objective weights (for a
    preference-conditioned policy).

    No elapsed frame or money spent so far: the best next move depends only on the current state,
    and with every training game starting alike the trees used the frame as a stand-in for it,
    so a live game running 40 frames ahead of the sim took a different branch (version 3).
    Version 4 (2026-11-06): oil derricks left for an engineer, and harvesters without the derricks'
    income streams."""
    s, db, n = obs.state, env.db, len(env.vocab)
    out, drain = env.sim.power(s.owned)
    gatherers = sum(1 for h in s.harvesters if len(h) < 6)  # a derrick's income is not a harvester
    derricks = len(env.sim.cal.derricks) - s.derricks_sent  # still free for an engineer (version 4)
    head = [s.money / 1000, out / 100, drain / 100, gatherers, len(s.dock_free), derricks]
    owned = np.zeros(n)
    making = np.zeros(n)
    want = np.zeros(n)
    for tid, i in env.index.items():
        owned[i - 1] = s.owned[tid]
        want[i - 1] = max(0, obs.targets.get(tid, 0) - s.owned[tid])
    for job in s.jobs.values():
        if job and job.techno.id in env.index:
            making[env.index[job.techno.id] - 1] = 1 - job.progress if job.placing_until is None else 0.01
    path = np.zeros(n)
    for tid, k in needed_items(env, obs).items():
        if tid in env.index:
            path[env.index[tid] - 1] = k
    cat = [float(obs.category == c) for c in CATEGORIES]
    w = weights or {}
    pref = [w.get("frames", 1.0), w.get("spent", 0.0)]
    # Whether the money covers the rest of the path (e.g. is a second factory affordable): a sum
    # over the path block that trees cannot form from separate columns.
    cost = float(path @ env.costs) / 1000
    budget = [cost, cost - s.money / 1000]
    return np.concatenate([head, owned, making, want, path, cat, pref, budget]).astype(np.float32)


# --------------------------------------------------------------------------- live games
def state_from_snapshot(sim: EconomySim, snap) -> EconomyState:
    """EconomyState for a live game (memory.Snapshot): on-map counts, credits, factory jobs.

    Harvesters are restarted half a cycle in, since the
    snapshot does not say how full they are.
    """
    db = sim.db
    owned: Counter = Counter()
    for o in snap.objects:
        if o.owner == snap.player_index and o.health > 0 and o.on_map and o.type_id in db.technos:
            owned[o.type_id] += 1
    state = EconomyState(frame=float(snap.frame), owned=owned,
                         money=float(snap.player.credits if snap.player else 0))
    for f in sorted(snap.factories, key=lambda f: f.category == "defense"):
        cat = {"naval": "vehicle", "defense": "building"}.get(f.category, f.category)
        if cat == "building" and state.jobs.get(cat) is not None:
            continue  # the sim has one building queue; the game's defence queue runs alongside it
        if f.type_id in db.technos and cat in CATEGORIES:
            t = db.technos[f.type_id]
            job = _Job(t, db.build_frames(t), progress=f.progress)
            if cat == "building" and f.progress >= 1.0:
                job.placing_until = state.frame + sim.cal.place_delay_frames
            state.jobs[cat] = job
    for tid, n in owned.items():
        t = db.technos[tid]
        if t.kind == "building" and t.storage and t.free_unit:
            state.dock_free.extend([state.frame] * n)
    for tid, n in list(owned.items()):
        for _ in range(n):
            sim._add_gatherer(state, db.technos[tid], state.frame - 0.5 * _cycle(sim, db.technos[tid]))
    # An engineer is used up by its capture: count each derrick we hold as the engineer that took
    # it, or a target with engineers would be rebuilt forever (as in the sim, which keeps them).
    taken = owned.get(sim.cal.derrick_type, 0)
    if taken:
        state.derricks_sent = taken
        eng = next((t.id for t in db.technos.values() if t.engineer and sim.buildable(t.id)), None)
        if eng:
            state.owned[eng] += taken
    return state


def _cycle(sim: EconomySim, t: Techno) -> float:
    if t.enslaves and t.slaves_number:
        return sim.cal.slave_cycle_frames
    if t.harvester and t.kind != "building":
        return sim._harvest_cycle(t)[0]
    return 0.0
