"""Optimisation problems: army composition and build order, each with pluggable constraints."""
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field

from .combat import Battle
from .economy import HORIZON, EconomySim, EconomyState
from .gamedata import GameData
from .optimize import Evaluation
from .params import Calibration


@dataclass
class EnemyScenario:
    name: str
    units: dict[str, int]
    weight: float = 1.0


@dataclass
class CombatSettings:
    distance: float = 12.0
    dt: int = 5
    max_frames: int = 5400
    crush: bool = True
    kite: bool = True      # fast, longer-ranged units back off while reloading (player micro)
    spacing: float = 1.5   # formation spacing in cells; larger = less splash damage
    b_damage: float = 1.0  # side B (the enemy) damage multiplier, see Battle
    focus: bool = False    # our side focus-fires like assault.py (Battle.run)
    base_spacing: float = 0.0  # buildings this far apart behind their units (0: packed with them)


# --------------------------------------------------------------------------- army
@dataclass
class ArmyProblem:
    """Choose unit counts that beat a set of enemy armies under resource/time constraints.

    Constraints (all optional):
      budget          total cost <= budget
      max_units       unit count <= max_units (also min_units)
      max_per_unit    {id: n} hard caps;  min_per_unit {id: n} hard minimums
      min_aa_units    at least n units able to shoot aircraft
      min_score       every enemy scenario must reach this score (0 = must win/draw each)
      deadline_frames army must be buildable within this time (economy sim, default build order)
    """
    db: GameData
    country: str
    enemies: list[EnemyScenario]
    candidates: list[str]
    cal: Calibration = field(default_factory=Calibration)
    combat: CombatSettings = field(default_factory=CombatSettings)
    budget: float | None = None
    max_units: int | None = None
    min_units: int = 1
    max_per_unit: dict[str, int] = field(default_factory=dict)
    min_per_unit: dict[str, int] = field(default_factory=dict)
    min_aa_units: int = 0
    min_score: float | None = None
    deadline_frames: int | None = None
    economy_prefix: list[str] = field(default_factory=list)
    aggregate: str = "mean"  # "mean" or "worst"
    cost_weight: float = 0.0

    def __post_init__(self):
        self.costs = [self.db.technos[c].cost for c in self.candidates]
        self.cap = [self.max_per_unit.get(c, self.max_units or 30) for c in self.candidates]
        self.cap = [min(cap, t.build_limit) if (t := self.db.technos[c]).build_limit > 0 else cap
                    for cap, c in zip(self.cap, self.candidates)]
        self.floor = [self.min_per_unit.get(c, 0) for c in self.candidates]
        self.anti_air = [any(w.aa for w in self.db.technos[c].combat_weapons) for c in self.candidates]

    # genome = tuple of counts aligned with self.candidates
    def key(self, genome):
        return genome

    def _clamp(self, counts: list[int]) -> tuple:
        return tuple(min(max(c, lo), hi) for c, lo, hi in zip(counts, self.floor, self.cap))

    def random_genome(self, rng: random.Random) -> tuple:
        counts = list(self.floor)
        limit = (self.budget or 10000) * rng.uniform(0.5, 1.0)
        types = rng.sample(range(len(self.candidates)), k=min(len(self.candidates), rng.randint(1, 4)))
        spent = sum(c * k for c, k in zip(self.costs, counts))
        while True:
            i = rng.choice(types)
            if spent + self.costs[i] > limit or counts[i] >= self.cap[i]:
                break
            if self.max_units and sum(counts) >= self.max_units:
                break
            counts[i] += 1
            spent += self.costs[i]
        return self._clamp(counts)

    def mutate(self, genome: tuple, rng: random.Random) -> tuple:
        counts = list(genome)
        for _ in range(rng.randint(1, 2)):
            i = rng.randrange(len(counts))
            r = rng.random()
            if r < 0.4:
                counts[i] += rng.choice((-1, 1))
            elif r < 0.8:
                j = rng.randrange(len(counts))
                if counts[i] > 0:  # trade units of type i for roughly the same value of type j
                    counts[i] -= 1
                    counts[j] += max(1, round(self.costs[i] / self.costs[j]))
            else:
                counts[i] = rng.randint(0, 4)
        return self._clamp(counts)

    def crossover(self, a: tuple, b: tuple, rng: random.Random) -> tuple:
        return self._clamp([x if rng.random() < 0.5 else y for x, y in zip(a, b)])

    def army(self, genome: tuple) -> dict[str, int]:
        return {c: n for c, n in zip(self.candidates, genome) if n > 0}

    def evaluate(self, genome: tuple) -> Evaluation:
        army = self.army(genome)
        cost = sum(c * n for c, n in zip(self.costs, genome))
        units = sum(genome)
        v: dict[str, float] = {}
        if units < self.min_units:
            v["min_units"] = float(self.min_units - units)
        if self.budget is not None and cost > self.budget:
            v["budget"] = (cost - self.budget) / self.budget
        if self.max_units is not None and units > self.max_units:
            v["max_units"] = float(units - self.max_units)
        aa = sum(n for n, ok in zip(genome, self.anti_air) if ok)
        if aa < self.min_aa_units:
            v["min_aa_units"] = float(self.min_aa_units - aa)

        scores = {}
        for e in self.enemies:
            c = self.combat
            res = Battle(self.db, army, e.units, self.cal, c.distance, c.spacing, base_spacing=c.base_spacing).run(
                c.dt, c.max_frames, c.crush, c.kite, c.focus)
            scores[e.name] = res.score
        if self.aggregate == "worst":
            agg = min(scores.values())
        else:
            total_w = sum(e.weight for e in self.enemies)
            agg = sum(scores[e.name] * e.weight for e in self.enemies) / total_w
        if self.min_score is not None:
            short = sum(max(0.0, self.min_score - s) for s in scores.values())
            if short > 0:
                v["min_score"] = short

        finish = None
        if self.deadline_frames is not None and army:
            sim = EconomySim(self.db, self.country, self.cal)
            res = sim.run(self.economy_prefix, army, max_frames=self.deadline_frames * 2)
            finish = res.finish_frame
            if finish is None:
                v["deadline"] = 1.0 + res.remaining_target_cost / max(cost, 1)
            elif finish > self.deadline_frames:
                v["deadline"] = (finish - self.deadline_frames) / self.deadline_frames

        objective = -agg + self.cost_weight * cost / (self.budget or 10000)
        return Evaluation(objective, v, {"scores": scores, "cost": cost, "units": units,
                                         "anti_air": aa, "finish_frame": finish})


@dataclass
class MissionProblem(ArmyProblem):
    """Pareto version of plan mode: an army judged on three objectives at once, all minimised:
    -(win margin) against the enemy scenarios, the frame the whole army is ready, credits spent
    (army plus the buildings it needs).

    `planner(army) -> (finish frame or None, spent)` builds the army in the economy sim. The default
    is resolve()'s order; the trained build policy (live_policy.simulate) is close to the GA
    teacher at a fraction of the cost. ArmyProblem's constraints still apply (deadline_frames
    uses the planner's finish frame).
    """
    planner: object = None

    def plan(self, army: dict[str, int]) -> tuple[float | None, float]:
        if self.planner is not None:
            out = self.planner(army)
            self.last_events = getattr(self.planner, "last_events", None)
            return out
        res = EconomySim(self.db, self.country, self.cal).run(self.economy_prefix, army,
                                                              max_frames=HORIZON)
        self.last_events = list(res.events)
        return res.finish_frame, res.spent

    def evaluate(self, genome: tuple) -> Evaluation:
        deadline, self.deadline_frames = self.deadline_frames, None  # checked below with the planner
        try:
            ev = super().evaluate(genome)
        finally:
            self.deadline_frames = deadline
        army = self.army(genome)
        finish, spent = self.plan(army) if army else (0.0, 0.0)
        if finish is None:
            ev.violations["unfinished"] = 1.0
            finish = HORIZON
        elif deadline is not None and finish > deadline:
            ev.violations["deadline"] = (finish - deadline) / deadline
        margin = ev.objective + self.cost_weight * ev.info["cost"] / (self.budget or 10000)  # = -agg
        ev.objectives = (margin, float(finish), float(spent))
        ev.info.update(finish_frame=finish, spent=spent)
        return ev


@dataclass
class VictoryProblem(MissionProblem):
    """Stage 6 (破關): the army that wins the game soonest. The army (plus the skirmish's free
    units) sets off as soon as it is built, marches to the AI base and fights whatever the AI has
    on arrival, units and every building (ShortGame: razing the base wins). The opponent's
    strength over time comes from a live game (opponent.Opponent), so attacking before the AI
    builds its defences is rewarded.

    One opponent: objectives (victory frame, credits spent, -share of our army left), and losing
    is a constraint violation sized by how much of the AI survives, which steers the search.

    Several opponents (`opponents`, e.g. easy / medium / hard AI): one victory-frame objective
    each, then spent and -(mean share left). A loss counts as LOSS_FRAMES plus how much of that
    AI survives, so the front keeps both armies that only beat the easy AI quickly and armies
    that beat every difficulty; only losing to all of them is a violation. A preference over the
    difficulties then picks the plan (mission.choose_victory).
    """
    opponent: object = None        # opponent.Opponent
    opponents: list = field(default_factory=list)  # several opponents; `opponent` alone is the one-AI case
    travel_factor: float = 1.3     # path length / straight-line distance for ground units
    battle_frames: int = 20000
    # The AI keeps playing (calibrated on the live losses vs the hard AI, 2026-11-01):
    reinforce: bool = True         # its timeline's new units and buildings join the fight
    rebuild: bool = True           # it rebuilds lost buildings while its construction yard stands
    counterattack: bool = True     # its attack waves (Opponent.attacks) meet our army first
    storm: bool = True             # its Lightning Storm delays our build (VictoryProblem.stormed)
    chrono: bool = True            # its Chronosphere drops tanks next to our base (VictoryProblem.chronos)
    # Starting infantry the live Steward garrisons in the civilian building next to our ore field
    # and derrick (2026-11-06): away from the base, so not in the home guard here.
    garrison: int = 0
    # Early defence (2026-11-02): no plan built tech-first held the q80 hard AI's first wave at
    # ~9800, when only the start army was out. These extra genes are built BEFORE the army:
    # units join it, buildings (Tesla coils, sentry guns, flak cannons) stay home as its guard.
    early: list[str] = field(default_factory=list)
    max_early: int = 6             # per type
    # Robust mode (2026-11-02): `opponents` are the recorded games themselves and the objectives
    # are (-share of games won, mean victory frame with losses at LOSS_FRAMES+, spent, -left).
    # A plan beating the q80 composite won only 5 of the 10 games: per-slot quantiles taken
    # from different games do not make an opponent that beats 80% of them.
    robust: bool = False
    # ArmyProblem.deadline_frames: here the army must be ready by then. Every live win vs the hard
    # AI came from armies gathered by ~16000 (1 Prism Tower up); every later one lost (2026-11-09),
    # and past ~20000 most recorded games have ended, so the AI there is extrapolated.
    # One wave (2026-11-10, user): a first wave that fails leaves a stronger AI with superweapons
    # (series_v11 game 1: 32 -> 25 buildings, the 2nd wave did less). Robust mode only: a game
    # counts as won only if our army keeps `min_left` of its value, and fewer than `min_win_rate`
    # of the games won that way is the violation "one_wave" (the sim is optimistic, hence a margin).
    min_win_rate: float | None = None
    min_left: float = 0.0

    LOSS_FRAMES = 60000.0  # above any win: builds may run to HORIZON, then march and fight

    def __post_init__(self):
        super().__post_init__()
        if not self.opponents and self.opponent is not None:
            self.opponents = [self.opponent]
        self.opponent = self.opponent or self.opponents[0]
        self.n_main = len(self.candidates)
        for t in self.early:  # genome = main counts, then early counts
            tech = self.db.technos[t]
            self.costs.append(tech.cost)
            cap = min(self.max_early, tech.build_limit) if tech.build_limit > 0 else self.max_early
            self.cap.append(min(cap, len(self.cal.derricks)) if tech.engineer else cap)
            self.floor.append(0)
            self.anti_air.append(any(w.aa for w in tech.combat_weapons))

    def random_genome(self, rng: random.Random) -> tuple:
        """ArmyProblem's random army; half the time also a random early defence of 1-2 types."""
        g = list(super().random_genome(rng))
        if self.early and rng.random() < 0.5:
            for i in rng.sample(range(self.n_main, len(g)), k=min(2, len(self.early))):
                g[i] = rng.randint(1, self.cap[i])
        return self._clamp(g)

    def genome_of(self, army: dict[str, int], early: dict[str, int] | None = None) -> tuple:
        early = early or {}
        return self._clamp([int(army.get(c, 0)) for c in self.candidates]
                           + [int(early.get(c, 0)) for c in self.early])

    def economy(self, tid: str) -> bool:
        """Harvesters, refineries and engineers (oil derricks) in `early`: built first for income
        (the home ore runs out around frame 13000), neither marching with the army nor guarding the base."""
        t = self.db.technos[tid]
        free = self.db.technos.get(t.free_unit) if t.free_unit else None
        return t.engineer or (t.harvester and t.kind != "building") or (t.kind == "building" and free is not None and free.harvester)

    def early_of(self, genome: tuple) -> dict[str, int]:
        return {c: n for c, n in zip(self.early, genome[self.n_main:]) if n > 0}

    def plan(self, army: dict[str, int], early: dict[str, int] | None = None) -> tuple[float | None, float]:
        if not early:
            return super().plan(army)
        out = self.planner(army, early)
        self.last_events = getattr(self.planner, "last_events", None)
        return out

    def home_guard(self, built) -> dict[str, int]:
        """The free starting infantry of types the plan does not build stays home (live:
        execute.py keeps them there with the Defence controller)."""
        g = home_guard(self.db, Counter(built), self.opponent.player_start_army)
        left = self.garrison
        for t in sorted(g):
            if left > 0 and self.db.technos[t].occupier:
                k = min(left, g[t])
                g[t] -= k
                left -= k
        return {t: n for t, n in g.items() if n}

    def march_frames(self, army: dict[str, int], opponent=None) -> float:
        speeds = [self.db.technos[t].speed * self.cal.speed_to_cells for t in army]
        ground = any(not self.db.technos[t].is_air for t in army)
        slowest = min(s for s in speeds if s > 0) if any(s > 0 for s in speeds) else 0.0
        if slowest <= 0:
            return float("inf")
        return (opponent or self.opponent).base_distance * (self.travel_factor if ground else 1.0) / slowest

    def first_air(self, built: dict[str, int], finish: float) -> float | None:
        """When our first aircraft is out, assuming the aircraft come last, one after another."""
        air = [self.db.technos[t] for t, n in built.items() for _ in range(n) if self.db.technos[t].is_air]
        if not air:
            return None
        return finish - sum(self.db.build_frames(t) for t in air) + min(self.db.build_frames(t) for t in air)

    def answered(self, opponent, frame: float, air_at: float | None) -> Counter:
        """What the AI has added by `frame` to answer our army (Opponent.responses)."""
        return Counter(opponent.response("air", air_at, frame)) if air_at is not None else Counter()

    def spawns(self, opponent, arrive: float, air_at: float | None = None) -> list[tuple[float, str]]:
        """What the AI adds after `arrive` (its timeline's growth and its answers to our army),
        as Battle spawns."""
        if not self.reinforce:
            return []
        units, buildings = opponent.at(arrive)
        seen = Counter(units) + Counter(buildings)
        out = []
        for f, u, b in zip(opponent.frames, opponent.units, opponent.buildings):
            if f <= arrive or f > arrive + self.battle_frames:
                continue
            now = Counter(u) + Counter(b)
            for t, n in now.items():
                out += [(f - arrive, t)] * max(0, n - seen[t])
            seen |= now  # the timeline's own losses do not count as new production
        had = self.answered(opponent, arrive, air_at)
        for f in range(int(arrive) + 100, int(arrive + self.battle_frames), 100):
            now = self.answered(opponent, f, air_at)
            out += [(f - arrive, t) for t, n in (now - had).items() for _ in range(n)]
            had = now
        return out

    # The AI's Lightning Storm (live vs hard 2026-11-06): its Weather Controller stood at 25627, and
    # our refinery + war factory vanished with no enemy near at 38128, 47186 and 56265: the first
    # strike ~12500 after it appears, then every RechargeTime (10 min = 9000 frames). Each strike
    # before our army is out costs rebuilding the factory and refinery (STORM_KILLS).
    STORM_FIRST = 12500
    STORM_EVERY = 9000
    WEATHER = ("GAWEAT",)
    STORM_KILLS = ("factory", "proc")

    def storms(self, opponent) -> list[float]:
        """Frames of the AI's lightning strikes on our base (none if it never had the device)."""
        built = next((f for f, b in zip(opponent.frames, opponent.buildings)
                      if any(t in b for t in self.WEATHER)), None)
        if built is None:
            return []
        return [built + self.STORM_FIRST + k * self.STORM_EVERY for k in range(8)]

    def stormed(self, finish: float, opponent) -> tuple[float, int]:
        """`finish` pushed back by the strikes that come before it, and how many came."""
        basics = self.db.basics(self.country)
        delay = sum(self.db.build_frames(self.db.technos[basics[k]]) for k in self.STORM_KILLS if k in basics)
        hits = 0
        for frame in self.storms(opponent):
            if frame >= finish:
                break
            finish += delay
            hits += 1
        return finish, hits

    # The AI's Chronosphere (series_v4-v7, 2026-11-06/07): in 19 of 39 games 4-6 tanks (Grizzlies,
    # a Prism Tank, a Mirage) appeared 1-4 cells from our buildings, first 10-15k frames after the
    # device stood (median ~13000), then every 7-12k. Under ShortGame the guard alone must hold them.
    CHRONO_FIRST = 13000
    CHRONO_EVERY = 9000
    CHRONOSPHERE = ("GACSPH",)
    CHRONO_UNITS = {"MTNK": 4, "SREF": 1, "MGTK": 1}

    def chronos(self, opponent) -> list[dict]:
        """The AI's chronoshifted tank drops on our base, as attack waves (none without the device)."""
        built = next((f for f, b in zip(opponent.frames, opponent.buildings)
                      if any(t in b for t in self.CHRONOSPHERE)), None)
        if built is None:
            return []
        units = {t: n for t, n in self.CHRONO_UNITS.items() if t in self.db.technos}
        return [{"frame": built + self.CHRONO_FIRST + k * self.CHRONO_EVERY, "units": units, "chrono": True}
                for k in range(6)]

    def attacks(self, opponent) -> list[dict]:
        """Its attack waves on our base in time order, chrono drops included."""
        if not self.counterattack:
            return []
        return sorted(opponent.attacks + (self.chronos(opponent) if self.chrono else []), key=lambda w: w["frame"])

    # A wave must raze our base (units and buildings) within this many frames to win: later, the
    # units made meanwhile, the recall of those near home and repairs hold it. In 22 games of
    # series_v6/v7/v10 none fell within 10000 frames of the army leaving (2026-11-09; was 300).
    BASE_HOLDS = 6000
    # Frames from a saboteur raid's first sighting near our base to the C4 on the construction
    # yard (live vs hard 2026-11-02: Tanya seen 20 cells out at 10736, yard gone at 11009).
    RAID_FUSE = 300

    def _home_loss(self, frame: float) -> float:
        """b_left for a base lost at `frame` before the attack: 0.7-1, worse the earlier, so the
        search can tell holding until the 2nd wave from dying to the 1st (all scored 1 before).
        Kept within a lost battle's 0-1 scale: scored above 1 the search fled to rushes that die
        at the AI base before any wave comes."""
        return 1.0 - 0.3 * min(1.0, frame / 20000.0)

    def at_home(self, army: Counter, guard: Counter, start: Counter, frame: float, finish: float,
                events: list | None = None) -> Counter:
        """Who is home to fight at `frame`: the guard and the free starting units, plus the plan's
        units out by then: from the build plan's `events` (frame, "done", type) when given, else
        taken as coming out evenly until `finish`. Before 2026-11-02 the whole finished army met
        waves that came long before it was built; live, the tech came first and at 10545 only 4
        of 18 units were out."""
        if events is not None:  # early towers in the guard stand only once built
            towers = Counter({t: n for t, n in guard.items() if self.db.technos[t].kind == "building"})
            up = Counter(t for f, kind, t in events if kind == "done" and f <= frame and t in towers)
            guard = guard - towers + (up & towers)
        if frame >= finish:
            return army + guard
        free = army & start
        built = army - free
        if events is not None:
            out = Counter(t for f, kind, t in events if kind == "done" and f <= frame and t in built)
            return guard + free + (out & built)
        frac = max(0.0, frame / finish) if finish > 0 else 1.0
        return guard + free + Counter({t: int(n * frac) for t, n in built.items() if int(n * frac)})

    def raid_held(self, home: Counter, raid: dict[str, int]) -> bool:
        """Do `home`'s units beat the raid, and kill its saboteurs within RAID_FUSE frames?"""
        from .opponent import SABOTEURS
        c = self.combat
        if not home:
            return False
        res = Battle(self.db, dict(home), raid, self.cal, c.distance, c.spacing, base_spacing=c.base_spacing).run(
            c.dt, self.battle_frames, c.crush, c.kite, c.focus)
        if res.winner != "A":
            return False
        sab = {t: n for t, n in raid.items() if t in SABOTEURS}
        if not sab:
            return True
        res = Battle(self.db, dict(home), sab, self.cal, c.distance, c.spacing, base_spacing=c.base_spacing).run(
            c.dt, self.RAID_FUSE, c.crush, c.kite, c.focus)
        return res.winner == "A" and res.frames <= self.RAID_FUSE

    def base_at(self, frame: float, events: list | None, skip=()) -> Counter:
        """Our buildings standing at `frame`: the construction yard plus the plan's buildings done
        by then (`events`), else the basics the army needs. `skip`: types already fighting (towers)."""
        b = self.db.basics(self.country)
        if events is None:
            base = Counter(b[k] for k in ("cnst", "power", "proc", "barracks", "factory") if k in b)
        else:
            base = Counter({b["cnst"]: 1}) + Counter(t for f, kind, t in events if kind == "done" and f <= frame
                                                     and self.db.technos[t].kind == "building")
        return Counter({t: n for t, n in base.items() if t not in skip})

    def defend(self, home: Counter, wave: dict[str, int], frame: float, events: list | None):
        """A wave at our base, met by `home` with the buildings standing behind it. Returns
        (held, units of `home` left, frames). The base is lost only if the wave razes it within
        BASE_HOLDS: until 2026-11-10 any wave the units could not wipe out lost the match, so two
        Harriers over a base with no anti-air, or one GI at an empty base after the army left,
        decided 25 of 40 games against the v12 plan, while live no base fell in 25 games."""
        c = self.combat
        base = self.base_at(frame, events, skip=home)
        res = Battle(self.db, dict(home + base), wave, self.cal, c.distance, c.spacing,
                     base_spacing=c.base_spacing).run(c.dt, self.BASE_HOLDS, c.crush, c.kite, c.focus)
        left = Counter({t: min(n, home[t]) for t, n in res.a_survivors.items() if home[t]})
        return res.winner != "B", left, res.frames

    def fight(self, army: dict[str, int], finish: float, opponent, guard: dict[str, int] | None = None,
              events: list | None = None) -> dict:
        """Meet the AI's attack waves that come before we arrive, march to `opponent` with
        whoever survives and fight its base, which keeps producing and rebuilding.

        `guard` stays home: it helps against waves before we leave, and alone holds the base
        against the waves that come while the army is away. Under ShortGame a razed base loses
        the match, which happened live when a wave came just before the predicted victory."""
        c = self.combat
        finish, storms = self.stormed(finish, opponent) if self.storm else (finish, 0)
        guard = Counter(guard or {})
        army = start_army = Counter(army)
        free = Counter(opponent.player_start_army)
        value = lambda a: sum(self.db.technos[t].cost * n for t, n in a.items())
        start = value(army) or 1.0
        away = Counter()       # AI units that died attacking us (already gone from the timeline)
        depart = finish        # we set off once built and done with any wave that came first
        arrive = depart + self.march_frames(army, opponent)
        raids = opponent.raids if self.counterattack else []
        hits = sorted([(w["frame"], 0, w) for w in self.attacks(opponent)]
                      + [(r["frame"], 1, r) for r in raids], key=lambda h: (h[0], h[1]))
        for frame, is_raid, wave in hits:  # in time order: a raid meets who survived the waves before it
            # Once the army is out it keeps going and the guard alone holds home (below), as live:
            # until 2026-11-09 a wave that came while it marched pulled it home first, which put
            # the series_v6 plan's arrival ~5000 frames after the live one.
            if not math.isfinite(arrive) or frame >= depart:
                break
            if is_raid:
                if frame < depart and not self.raid_held(
                        self.at_home(army, guard, free, frame, finish, events), wave["units"]):
                    return {"arrive_frame": arrive, "won": False, "b_left": self._home_loss(frame), "a_left": 0.0,
                            "victory_frame": None, "lost_to_raid": frame, "survivors": {}}
                continue  # after departure: the guard alone, below
            home = self.at_home(army, guard, free, wave["frame"], finish, events)
            held, left, frames = self.defend(home, wave["units"], wave["frame"], events)
            if not held:
                return {"arrive_frame": arrive, "won": False, "b_left": self._home_loss(wave["frame"]),
                        "a_left": 0.0, "victory_frame": None, "lost_to_wave": wave["frame"], "survivors": {}}
            total = army + guard - (home - left)  # units not yet built are safe
            guard = Counter({t: min(n, total[t]) for t, n in guard.items() if total[t]})
            army = total - guard
            away += Counter(wave["units"])
            depart = max(depart, wave["frame"] + frames)
            arrive = depart + self.march_frames(army, opponent)
        if not math.isfinite(arrive):
            return {"arrive_frame": arrive, "won": False, "b_left": 1.0, "a_left": 0.0, "victory_frame": None}
        units, buildings = opponent.at(arrive)
        air_at = self.first_air(start_army, finish)
        # The recorded timeline only holds live units, so the waves that died at our base are already
        # gone from it: taking them off again (until 2026-11-09) halved the AI army we met late in the
        # game (series_v10: sim $5-11k at arrival, live $12-19k) and made late arrivals look like wins.
        enemy = {**Counter(units), **(Counter(buildings) + self.answered(opponent, arrive, air_at))}
        res = Battle(self.db, dict(army), enemy, self.cal, c.distance, c.spacing, base_spacing=c.base_spacing,
                     spawns=self.spawns(opponent, arrive, air_at), rebuild=self.rebuild,
                     b_damage=c.b_damage, replace_rate=getattr(opponent, "replace_rate", None)).run(
            c.dt, self.battle_frames, c.crush, c.kite, c.focus)
        won = res.winner == "A"
        lost_home = None
        end = arrive + res.frames
        for wave in self.attacks(opponent):
            if not depart <= wave["frame"] < end:
                continue
            held, guard, frames = self.defend(guard, wave["units"], wave["frame"], events)
            if not held:
                lost_home = wave["frame"] + frames
                break
        for raid in raids:  # and while it is away, the guard alone
            if depart <= raid["frame"] < end and not self.raid_held(guard, raid["units"]):
                fell = raid["frame"] + self.RAID_FUSE
                lost_home = fell if lost_home is None else min(lost_home, fell)
                break
        if won and lost_home is not None and lost_home < end:
            won = False  # the base fell before the AI did
        kept = value(army) / start  # share of the army that survived the waves
        return {"arrive_frame": arrive, "depart_frame": depart, "won": won, "enemy": enemy,
                "battle_frames": res.frames, "storms": storms, "ready_frame": finish,
                "victory_frame": arrive + res.frames if won else None,
                "a_left": res.a_value_left * kept, "b_left": res.b_value_left,
                "survivors": res.a_survivors, "waves_met": sum(away.values()), "base_lost": lost_home}

    def evaluate(self, genome: tuple) -> Evaluation:
        built = self.army(genome)
        early = self.early_of(genome)
        fighting = {t: n for t, n in early.items() if not self.economy(t)}
        early_units = {t: n for t, n in fighting.items() if self.db.technos[t].kind != "building"}
        towers = {t: n for t, n in fighting.items() if self.db.technos[t].kind == "building"}
        built = dict(Counter(built) + Counter(early_units))
        guard = dict(Counter(self.home_guard(built)) + Counter(towers))
        army = dict(Counter(built) + Counter(self.opponent.player_start_army) - Counter(self.home_guard(built)))
        cost = sum(c * n for c, n in zip(self.costs, genome))
        finish, spent = self.plan(self.army(genome), early) if built or towers else (0.0, 0.0)
        v: dict[str, float] = {}
        if finish is None:
            v["unfinished"] = 1.0
            finish = float(HORIZON)
        units = sum(built.values())
        if self.max_units is not None and units > self.max_units:
            v["max_units"] = float(units - self.max_units)
        events = getattr(self, "last_events", None) if built or towers else None
        firsts = getattr(self.planner, "options", [])[1:] if early else []
        if firsts:  # the early block really first (live: execute.py --early): the plan that fights best
            score = lambda fs: (-sum(f["won"] for f in fs),
                                sum(f["victory_frame"] if f["won"] else self.LOSS_FRAMES * (1 + f["b_left"]) for f in fs))
            best = None
            for f_, s_, ev_ in firsts:
                alt = [self.fight(army, f_, o, guard, ev_) for o in self.opponents]
                if best is None or score(alt) < score(best[0]):
                    best = (alt, f_, s_, ev_)
            fights, finish, spent, self.last_events = best
            v.pop("unfinished", None)  # only the plain plan ran out of time; this one finished
        else:
            fights = [self.fight(army, finish, o, guard, events) for o in self.opponents]
        if self.deadline_frames is not None and finish > self.deadline_frames:
            v["deadline"] = (finish - self.deadline_frames) / self.deadline_frames
        first = fights[0]
        info = {"cost": cost, "units": units, "finish_frame": finish, "spent": spent, "army": army,
                "guard": guard, "early": early, "depart_frame": first.get("depart_frame"),
                **{k: first.get(k) for k in ("arrive_frame", "enemy", "battle_frames", "victory_frame",
                                             "a_left", "b_left", "survivors")},
                "vs": {o.name: f for o, f in zip(self.opponents, fights)}}
        if not math.isfinite(first["arrive_frame"]):
            v["immobile"] = 1.0
        if len(self.opponents) == 1:
            if not first["won"]:
                v["lose"] = 1.0 + first["b_left"]
            victory = first["victory_frame"] or first["arrive_frame"] + first.get("battle_frames", 0)
            if not math.isfinite(victory):
                victory = 1e9
            return Evaluation(victory, v, info, objectives=(victory, float(spent), -first["a_left"]))
        if not any(f["won"] for f in fights):
            v["lose"] = 1.0 + min(f["b_left"] for f in fights)
        if self.robust:
            n = len(fights)
            rate = sum(f["won"] and f["a_left"] >= self.min_left for f in fights) / n
            if self.min_win_rate is not None and rate < self.min_win_rate:
                v["one_wave"] = self.min_win_rate - rate
            mean = sum(f["victory_frame"] if f["won"] else self.LOSS_FRAMES * (1 + f["b_left"]) for f in fights) / n
            info["win_rate"] = rate
            left = sum(f["a_left"] for f in fights) / n
            return Evaluation(mean, v, info, objectives=(-rate, mean, float(spent), -left))
        frames = tuple(f["victory_frame"] if f["won"] else self.LOSS_FRAMES * (1 + f["b_left"]) for f in fights)
        left = sum(f["a_left"] for f in fights) / len(fights)
        return Evaluation(frames[0], v, info, objectives=(*frames, float(spent), -left))


def home_guard(db, built, start_army: dict[str, int]) -> dict[str, int]:
    return {t: n for t, n in start_army.items() if db.technos[t].kind == "infantry" and not built.get(t)}


# ---------------------------------------------------------------------- build order
@dataclass
class BuildOrderProblem:
    """Find the build order that gets `targets` fastest.

    The genome is a priority list; EconomySim.resolve() inserts missing prerequisites and
    power plants, so every genome is executable.

    Constraints (all optional):
      deadline_frames  all targets done by this frame
      max_count        {id: n} never own more than n (e.g. at most 2 war factories)
      milestones       {id: frame} first one of id finished by frame
      max_spent        credits spent before finishing
      max_low_power_frames  only meaningful with auto_power = False
    """
    db: GameData
    country: str
    targets: dict[str, int]
    extras: list[str]
    cal: Calibration = field(default_factory=Calibration)
    max_frames: int = 27000
    auto_power: bool = True
    deadline_frames: int | None = None
    max_count: dict[str, int] = field(default_factory=dict)
    milestones: dict[str, int] = field(default_factory=dict)
    max_spent: float | None = None
    max_low_power_frames: int | None = None
    max_extras: int = 8
    # Plan from a mid-game state instead of a fresh construction yard (BuildEnv / live game).
    state: EconomyState | None = None
    # Multi-objective weights: minimise frames * w["frames"] + credits spent * w["spent"].
    weights: dict[str, float] | None = None

    def __post_init__(self):
        have = self.have()
        # Only what is still missing goes into genomes; resolve() adds whatever a genome lists.
        self.target_items = [t for t, n in self.targets.items() for _ in range(max(0, n - have[t]))]

    def have(self) -> Counter:
        """Owned or in production at the start of planning."""
        if self.state is None:
            return Counter()
        return self.state.owned + Counter(j.techno.id for j in self.state.jobs.values() if j)

    def key(self, genome):
        return genome

    def _sim(self) -> EconomySim:
        return EconomySim(self.db, self.country, self.cal)

    def random_genome(self, rng: random.Random) -> tuple:
        items = list(self.target_items)
        rng.shuffle(items)
        for _ in range(rng.randint(0, min(4, self.max_extras))):
            items.insert(rng.randint(0, len(items)), rng.choice(self.extras))
        return tuple(items)

    def mutate(self, genome: tuple, rng: random.Random) -> tuple:
        items = list(genome)
        r = rng.random()
        extras_in = sum(1 for x in items if x in self.extras and x not in self.targets)
        if r < 0.3 and extras_in < self.max_extras:
            items.insert(rng.randint(0, len(items)), rng.choice(self.extras))
        elif r < 0.5 and items:
            items.pop(rng.randrange(len(items)))
        elif r < 0.8 and len(items) > 1:
            i, j = rng.randrange(len(items)), rng.randrange(len(items))
            items[i], items[j] = items[j], items[i]
        elif items:
            x = items.pop(rng.randrange(len(items)))
            items.insert(rng.randint(0, len(items)), x)
        return tuple(items)

    def crossover(self, a: tuple, b: tuple, rng: random.Random) -> tuple:
        i, j = rng.randint(0, len(a)), rng.randint(0, len(b))
        return tuple(a[:i] + b[j:])

    def evaluate(self, genome: tuple) -> Evaluation:
        sim = self._sim()
        have = self.have() if self.state is not None else None
        seq = sim.resolve(list(genome), self.targets, self.auto_power, have=have)
        res = sim.run(seq, self.targets, self.max_frames, resolved=True, state=self.state)
        v: dict[str, float] = {}
        if res.finish_frame is None:
            v["unfinished"] = 1.0 + res.remaining_target_cost / 1000
        elif self.deadline_frames is not None and res.finish_frame > self.deadline_frames:
            v["deadline"] = (res.finish_frame - self.deadline_frames) / self.deadline_frames
        for tid, cap in self.max_count.items():
            over = res.owned[tid] - cap
            if over > 0:
                v[f"max_count:{tid}"] = float(over)
        for tid, frame in self.milestones.items():
            done = res.first_done(tid)
            if done is None or done > frame:
                v[f"milestone:{tid}"] = ((done or self.max_frames * 2) - frame) / frame
        if self.max_spent is not None and res.spent > self.max_spent:
            v["max_spent"] = (res.spent - self.max_spent) / self.max_spent
        if self.max_low_power_frames is not None and res.low_power_frames > self.max_low_power_frames:
            v["low_power"] = (res.low_power_frames - self.max_low_power_frames) / 900
        # Tie-breaker: among equally fast orders prefer the cheaper one (1 frame per $1000).
        finish = res.finish_frame if res.finish_frame is not None else self.max_frames
        if self.weights:
            w = self.weights
            objective = w.get("frames", 1.0) * finish + w.get("spent", 0.0) * res.spent + res.spent / 1000
        else:
            objective = finish + res.spent / 1000
        return Evaluation(objective, v, {"sequence": seq, "result": res})
