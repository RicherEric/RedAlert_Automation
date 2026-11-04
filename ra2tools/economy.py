"""Base-building / economy simulator for build-order optimisation.

Modelled: per-category production queues (building/infantry/vehicle/aircraft) running in
parallel, gradual payment (production stalls when broke), BuildSpeed and BuildTimeMultiplier,
MultipleFactory speed-up, low-power slowdown, building placement delay, prerequisites,
refinery free harvesters with dock contention, chrono-miner teleport, Yuri slave miners.
Not modelled: ore depletion, base layout/travel distance changes, losing units, selling.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

from .gamedata import GameData, Techno
from .params import Calibration

CATEGORIES = ("building", "vehicle", "infantry", "aircraft")

# Generic prerequisite token -> key in SIDE_BASICS used when auto-inserting prerequisites.
TOKEN_TO_BASIC = {"POWER": "power", "PROC": "proc", "RADAR": "radar", "TECH": "tech",
                  "FACTORY": "factory", "BARRACKS": "barracks"}


class UnbuildableError(ValueError):
    pass


@dataclass
class EconomyResult:
    finish_frame: int | None  # when every target was owned; None if not reached
    end_frame: int
    owned: Counter
    money: float
    spent: float
    income: float
    min_power_balance: int
    low_power_frames: int
    events: list[tuple[int, str, str]] = field(default_factory=list)  # (frame, "start"/"done", id)
    remaining_target_cost: float = 0.0

    def first_done(self, tid: str) -> int | None:
        return next((f for f, kind, t in self.events if kind == "done" and t == tid), None)


@dataclass
class _Job:
    techno: Techno
    total_frames: float
    progress: float = 0.0
    placing_until: float | None = None


@dataclass
class EconomyState:
    """Everything the simulation needs to continue from a moment in the game."""
    frame: float
    owned: Counter
    money: float
    spent: float = 0.0
    income: float = 0.0
    harvesters: list[list] = field(default_factory=list)  # [next_income_frame, amount, cycle, uses_dock]
    dock_free: list[float] = field(default_factory=list)  # when each refinery dock frees up
    jobs: dict[str, _Job | None] = field(default_factory=lambda: {c: None for c in CATEGORIES})
    events: list[tuple[int, str, str]] = field(default_factory=list)
    min_balance: int = 0
    low_frames: int = 0
    queue_next: int = 0     # index into CATEGORIES of the next queue to tick
    tick_speed: float = 1.0  # production speed of the current tick
    ticking: bool = False   # tick_start already ran for the current frame

    def copy(self) -> "EconomyState":
        return replace(self, owned=Counter(self.owned), harvesters=[list(h) for h in self.harvesters],
                       dock_free=list(self.dock_free), events=list(self.events),
                       jobs={c: (replace(j) if j else None) for c, j in self.jobs.items()})


class EconomySim:
    def __init__(self, db: GameData, country: str, cal: Calibration | None = None, dt: int = 15):
        self.db = db
        self.country = country
        self.cal = cal or Calibration()
        self.dt = dt
        self.basics = db.basics(country)
        self._buildable: dict[str, bool] = {}

    # ------------------------------------------------------------ order repair
    def buildable(self, tid: str, _seen: frozenset = frozenset()) -> bool:
        """The country can own `tid` and every prerequisite has a buildable option."""
        if tid in self._buildable:
            return self._buildable[tid]
        t = self.db.technos.get(tid)
        ok = (t is not None and tid not in _seen and self.db.country_can_own(t, self.country)
              and all(any(o == self.basics["cnst"] or self.buildable(o, _seen | {tid})
                          for o in self.db.prereq_options(tok)) for tok in t.prerequisites))
        if not _seen:
            self._buildable[tid] = ok
        return ok

    def _choose_option(self, token: str) -> str:
        basic = TOKEN_TO_BASIC.get(token.upper())
        if basic and self.basics.get(basic) and self.buildable(self.basics[basic]):
            return self.basics[basic]
        for opt in self.db.prereq_options(token):
            t = self.db.technos.get(opt)
            if t and t.kind == "building" and self.buildable(opt):
                return opt
        raise UnbuildableError(f"no building satisfies prerequisite {token} for {self.country}")

    def resolve(self, order: list[str], targets: dict[str, int] | None = None,
                auto_power: bool = True, have: Counter | None = None) -> list[str]:
        """Return a buildable sequence: prerequisites inserted, unbuildable items dropped,
        missing targets appended, and (optionally) power plants added to avoid low power.
        `have` is what is already owned or in production (default: just the construction yard)."""
        db = self.db
        planned: Counter = Counter(have) if have else Counter({self.basics["cnst"]: 1})
        power = sum(db.technos[b].power * n for b, n in planned.items()
                    if b in db.technos and db.technos[b].kind == "building")
        out: list[str] = []

        def add(tid: str) -> None:
            nonlocal power
            t = db.technos.get(tid)
            if t is None or not db.country_can_own(t, self.country):
                raise UnbuildableError(tid)
            if t.build_limit > 0 and planned[tid] >= t.build_limit:
                return
            if not db.prereqs_met(t, planned):
                for tok in t.prerequisites:
                    if not any(o in planned for o in db.prereq_options(tok)):
                        add(self._choose_option(tok))
            if t.kind == "building" and auto_power and t.power < 0:
                while power + t.power < 0:
                    add(self.basics["power"])
            out.append(tid)
            planned[tid] += 1
            if t.kind == "building":
                power += t.power
                if t.free_unit:
                    planned[t.free_unit] += 1

        for tid in order:
            try:
                add(tid)
            except UnbuildableError:
                continue
        for tid, n in (targets or {}).items():
            while planned[tid] < n:
                before = planned[tid]
                add(tid)
                if planned[tid] == before:  # build limit reached
                    break
        return out

    # ----------------------------------------------------------------- running
    def _harvest_cycle(self, t: Techno) -> tuple[float, float]:
        """(frames before reaching the refinery dock, credits per load)."""
        cal = self.cal
        speed = max(t.speed * cal.speed_to_cells, 1e-6)
        travel = cal.ore_distance / speed
        load = t.storage * cal.harvest_frames_per_bale
        back = cal.chrono_return_frames if t.teleporter else travel
        return travel + load + back, t.storage * self.db.ore_value

    def initial_state(self) -> EconomyState:
        """Construction yard just deployed, starting credits, nothing else."""
        return EconomyState(frame=self.cal.mcv_deploy_frames, owned=Counter({self.basics["cnst"]: 1}),
                            money=float(self.cal.start_credits))

    def can_start(self, state: EconomyState, t: Techno) -> bool:
        """A factory for t's queue exists and its prerequisites are owned."""
        return bool(self._factory_count(state.owned, self.category(t))) and self.db.prereqs_met(t, state.owned)

    def power(self, owned: Counter) -> tuple[int, int]:
        """(output, drain) of the owned buildings."""
        technos = self.db.technos
        out = sum(technos[b].power * n for b, n in owned.items()
                  if technos[b].kind == "building" and technos[b].power > 0)
        drain = -sum(technos[b].power * n for b, n in owned.items()
                     if technos[b].kind == "building" and technos[b].power < 0)
        return out, drain

    def _schedule(self, state: EconomyState, h: list, now: float) -> None:
        arrival = now + h[2]
        if h[3] and state.dock_free:
            k = min(range(len(state.dock_free)), key=state.dock_free.__getitem__)
            start = max(arrival, state.dock_free[k])
            state.dock_free[k] = start + self.cal.unload_frames
            h[0] = start + self.cal.unload_frames
        else:
            h[0] = arrival

    def _add_gatherer(self, state: EconomyState, t: Techno, now: float) -> None:
        db, cal = self.db, self.cal
        if t.enslaves and t.slaves_number:
            slave = db.technos.get(t.enslaves)
            amount = t.slaves_number * (slave.storage if slave else 4) * db.ore_value
            h = [0.0, amount, cal.slave_cycle_frames, False]
        elif t.harvester and t.kind != "building":
            cycle, amount = self._harvest_cycle(t)
            h = [0.0, amount, cycle, True]
        else:
            return
        self._schedule(state, h, now)
        state.harvesters.append(h)

    def _complete(self, state: EconomyState, t: Techno, now: float) -> None:
        db = self.db
        state.owned[t.id] += 1
        state.events.append((int(now), "done", t.id))
        if t.kind == "building":
            if t.storage and (t.free_unit or t.enslaves) and not t.enslaves:
                state.dock_free.append(now)
            if t.free_unit and t.free_unit in db.technos:
                state.owned[t.free_unit] += 1
                self._add_gatherer(state, db.technos[t.free_unit], now)
        self._add_gatherer(state, t, now)

    def step(self, state: EconomyState, pick) -> None:
        """Simulate one tick of `dt` frames at state.frame (the caller advances the frame).

        `pick(category)` is asked for the next Techno whenever a queue is empty; it returns
        None to leave the queue idle this tick. Queues run in CATEGORIES order, so a building
        finished earlier in the tick can already enable a later queue. A state paused between
        queues (by BuildEnv) resumes where it stopped.
        """
        if not state.ticking:
            state.tick_speed = self.tick_start(state)
            state.ticking = True
        while state.queue_next < len(CATEGORIES):
            self.tick_queue(state, CATEGORIES[state.queue_next], pick)
            state.queue_next += 1
        state.queue_next = 0
        state.ticking = False

    def tick_start(self, state: EconomyState) -> float:
        """Income and power for this tick; returns the production speed factor (low power)."""
        db, dt = self.db, self.dt
        frame = state.frame
        for h in state.harvesters:
            while h[0] <= frame:
                state.money += h[1]
                state.income += h[1]
                self._schedule(state, h, h[0])

        power_out, drain = self.power(state.owned)
        state.min_balance = min(state.min_balance, power_out - drain)
        power_speed = 1.0
        if drain > power_out:
            state.low_frames += dt
            ratio = power_out / drain if drain else 1.0
            power_speed = 1 - (1 - ratio) * db.low_power_penalty
            power_speed = min(db.max_low_power_speed, max(db.min_low_power_speed, power_speed))
        return power_speed

    def tick_queue(self, state: EconomyState, cat: str, pick) -> None:
        """Advance one production queue by one tick (at state.tick_speed)."""
        db, cal, dt = self.db, self.cal, self.dt
        power_speed = state.tick_speed
        frame = state.frame
        job = state.jobs[cat]
        if job is None:
            t = pick(cat)
            if t is None:
                return
            job = state.jobs[cat] = _Job(t, db.build_frames(t))
            state.events.append((int(frame), "start", t.id))
        if job.placing_until is not None:
            if frame >= job.placing_until:
                self._complete(state, job.techno, frame)
                state.jobs[cat] = None
            return
        total = db.build_frames(job.techno, self._factory_count(state.owned, cat))
        inc = min(1.0 - job.progress, dt / total * power_speed)
        cost = job.techno.cost * inc
        if cost > state.money:
            inc = state.money / job.techno.cost if job.techno.cost else inc
            cost = state.money
        state.money -= cost
        state.spent += cost
        job.progress += inc
        if job.progress >= 1.0 - 1e-9:
            if cat == "building":
                job.placing_until = frame + cal.place_delay_frames
            else:
                self._complete(state, job.techno, frame)
                state.jobs[cat] = None

    def run(self, order: list[str], targets: dict[str, int], max_frames: int = 27000,
            auto_power: bool = True, resolved: bool = False,
            state: EconomyState | None = None) -> EconomyResult:
        """Build `order` (one queue per category, in order) until every target is owned.

        `state` continues from a mid-game state; it is copied, not modified."""
        db = self.db
        state = state.copy() if state else self.initial_state()
        have = state.owned + Counter(j.techno.id for j in state.jobs.values() if j)
        seq = order if resolved else self.resolve(order, targets, auto_power, have=have)
        queues = {c: [db.technos[t] for t in seq if self.category(db.technos[t]) == c] for c in CATEGORIES}
        heads = {c: 0 for c in CATEGORIES}
        targets = {k: v for k, v in targets.items() if v > 0}
        finish: int | None = None

        def pick(cat: str) -> Techno | None:
            if heads[cat] >= len(queues[cat]):
                return None
            t = queues[cat][heads[cat]]
            if not self.can_start(state, t):
                return None
            heads[cat] += 1
            return t

        while state.frame < max_frames:
            self.step(state, pick)
            if all(state.owned[t] >= n for t, n in targets.items()):
                finish = int(state.frame)
                break
            if all(state.jobs[c] is None and heads[c] >= len(queues[c]) for c in CATEGORIES):
                break  # nothing left to build but targets unmet (e.g. unbuildable)
            state.frame += self.dt

        remaining = sum(max(0, n - state.owned[t]) * db.technos[t].cost for t, n in targets.items())
        return EconomyResult(finish, int(state.frame), state.owned, state.money, state.spent, state.income,
                             state.min_balance, state.low_frames, state.events, remaining)

    def category(self, t: Techno) -> str:
        return t.kind if t.kind in CATEGORIES else "vehicle"

    _category = category

    def _factory_count(self, owned: Counter, cat: str) -> int:
        return sum(n for b, n in owned.items() if self.db.technos[b].factory == cat)
