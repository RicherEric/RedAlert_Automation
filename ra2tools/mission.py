"""Multi-objective mission planning (stage 5): which army to build, judged on winning, time and money.

MissionProblem's Pareto front holds every army that is not beaten on all three objectives; a
preference vector then picks one (Tchebycheff). The economy side is planned by the trained build
policy (models/build) when it is available, falling back to resolve()'s order whenever that is
faster, so a policy mistake on an unfamiliar army can only cost time, never correctness.
"""
from __future__ import annotations

from collections import Counter

from dataclasses import dataclass
from pathlib import Path

from .env import BuildEnv
from .gamedata import GameData
from .optimize import ParetoResult, run_nsga2, tchebycheff
from .params import Calibration
from .problems import CombatSettings, EnemyScenario, MissionProblem, VictoryProblem

OBJECTIVES = ("win", "frames", "spent")


@dataclass
class PolicyPlanner:
    """army -> (finish frame or None, spent), the better of the build policy and resolve()."""
    db: GameData
    country: str
    cal: Calibration
    model: str | None = None
    weights: dict | None = None

    def __post_init__(self):
        self._policy = None
        if self.model and Path(self.model, "policy.txt").exists():
            from .policy import BuildPolicy
            self._policy = BuildPolicy(Path(self.model))
        self.used_policy = 0
        self.calls = 0

    def __getstate__(self):  # LightGBM boosters do not pickle: workers reload the model
        d = dict(self.__dict__)
        d["_policy"] = None
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.__post_init__()

    def __call__(self, army: dict[str, int], early: dict[str, int] | None = None) -> tuple[float | None, float]:
        """The fastest plan for army + `early`. With `early` (VictoryProblem.early) the plans that
        build it first are kept too, in `self.options` [(finish, spent, events)]: building the
        defence first costs time, and only the fights can tell whether it is worth it."""
        from .economy import HORIZON, EconomySim
        from .live_policy import simulate

        self.calls += 1
        weights = self.weights or {"frames": 1.0, "spent": 0.0}
        total = dict(Counter(army) + Counter(early or {}))
        options = []
        res = EconomySim(self.db, self.country, self.cal).run([], total, max_frames=HORIZON)
        options.append((res.finish_frame, res.spent, list(res.events)))
        if self._policy is not None:
            env = BuildEnv(self.db, self.country, total, self.cal, vocab=self._policy.vocab, max_frames=HORIZON)
            events, frame, spent, done = simulate(env, self._policy, weights)
            if done:
                options.append((frame, spent, events))
                if options[0][0] is None or frame < options[0][0]:
                    options[0], options[-1] = options[-1], options[0]
                    self.used_policy += 1
        options = options[:1] if options[0][0] is not None else options[1:] or options[:1]
        if early:
            first = [t for t, n in early.items() for _ in range(n)]
            res = EconomySim(self.db, self.country, self.cal).run(first, total, max_frames=HORIZON)
            if res.finish_frame is not None:
                options.append((res.finish_frame, res.spent, list(res.events)))
            if self._policy is not None:  # the policy on the early block (+ harvesters), then the rest
                from .live_policy import first_stage
                stage = first_stage(self.db, self.country, early)
                state = self._run(stage, weights)
                if all(state.owned[t] >= n for t, n in stage.items()):
                    st = self._run(total, weights, state)
                    if all(st.owned[t] >= n for t, n in total.items()):
                        options.append((st.frame, st.spent, list(st.events)))
        self.options = options
        self.last_events = options[0][2]
        return options[0][0], options[0][1]

    def _run(self, targets: dict[str, int], weights: dict, state=None):
        from .economy import HORIZON
        """The build policy from `state` (default: the start) until `targets` are owned or time is up."""
        env = BuildEnv(self.db, self.country, targets, self.cal, vocab=self._policy.vocab, max_frames=HORIZON)
        obs = env.reset(state)
        while obs is not None:
            obs = env.step(self._policy.act(env, obs, weights))
        return env.state


def mission_problem(db: GameData, cfg: dict, cal: Calibration, model: str | None = None) -> MissionProblem:
    """MissionProblem from a scenario's [army] / [combat] sections (same keys as ra2opt army).
    [pareto] keys override [army] ones, and [pareto] ignore = [...] drops [army] constraints that
    are objectives here (budget, deadline_frames)."""
    p = cfg.get("pareto", {})
    a = {k: v for k, v in {**cfg.get("army", {}), **p}.items() if k not in p.get("ignore", [])}
    country = cfg["country"]
    exclude = set(a.get("exclude", []))
    candidates = [t.id for t in db.combat_units(country, include_air=a.get("include_air", True))
                  if t.id not in exclude]
    if a.get("only"):
        candidates = [c for c in candidates if c in set(a["only"])]
    enemies = [EnemyScenario(e.get("name", f"enemy{i}"), e["units"], e.get("weight", 1.0))
               for i, e in enumerate(a.get("enemy", []))]
    return MissionProblem(
        db, country, enemies, candidates, cal, CombatSettings(**cfg.get("combat", {})),
        budget=a.get("budget"), max_units=a.get("max_units"), min_units=a.get("min_units", 1),
        max_per_unit=a.get("max_per_unit", {}), min_per_unit=a.get("min_per_unit", {}),
        min_aa_units=a.get("min_aa_units", 0), min_score=a.get("min_score"),
        deadline_frames=a.get("deadline_frames"), aggregate=a.get("aggregate", "mean"),
        planner=PolicyPlanner(db, country, cal, model),
    )


VICTORY_OBJECTIVES = ("victory", "spent", "survive")


def victory_problem(db: GameData, cfg: dict, cal: Calibration, opponents, model: str | None = None) -> VictoryProblem:
    """VictoryProblem from a scenario's [victory] section (candidates, max_units, ...) and one or
    more opponent timelines (opponent.Opponent, e.g. one per AI difficulty)."""
    opponents = opponents if isinstance(opponents, list) else [opponents]
    v = cfg.get("victory", {})
    country = cfg["country"]
    exclude = set(v.get("exclude", []))
    candidates = [t.id for t in db.combat_units(country, include_air=v.get("include_air", True))
                  if t.id not in exclude]
    if v.get("only"):
        candidates = [c for c in candidates if c in set(v["only"])]
    return VictoryProblem(
        db, country, [], candidates, cal, CombatSettings(**{**cfg.get("combat", {}), **v.get("combat", {})}),
        max_units=v.get("max_units"), max_per_unit=v.get("max_per_unit", {}), min_units=0,
        planner=PolicyPlanner(db, country, cal, model), opponents=opponents,
        travel_factor=v.get("travel_factor", 1.3), reinforce=v.get("reinforce", True),
        rebuild=v.get("rebuild", True), counterattack=v.get("counterattack", True),
        early=list(v.get("early", [])), max_early=v.get("max_early", 6), robust=v.get("robust", False),
    )


def victory_objectives(problem: VictoryProblem) -> list[str]:
    """Objective names in order: one per opponent (its difficulty, else its name), spent, survive."""
    if getattr(problem, "robust", False):
        return ["win_rate", "frames", "spent", "survive"]
    if len(problem.opponents) == 1:
        return list(VICTORY_OBJECTIVES)
    tags = [o.difficulty or o.name for o in problem.opponents]
    names = [t if tags.count(t) == 1 else o.name for t, o in zip(tags, problem.opponents)]  # hard_q50, hard_q80
    return names + ["spent", "survive"]


def victory_seeds(problem: VictoryProblem, armies: list[dict[str, int]], top_k: int) -> list[tuple]:
    """TopK starting armies, after Wang et al. (CCC 2019): known plans (live wins, earlier search
    fronts) are scored in the simulator and the best top_k seed the initial population (the paper
    suggests about a quarter of it; the rest stays random for diversity)."""
    genomes = []
    for army in armies:  # an "early" key holds the early defence (VictoryProblem.early)
        army = dict(army)
        g = problem.genome_of({t: n for t, n in army.items() if t != "early"}, army.get("early"))
        if any(g) and g not in genomes:
            genomes.append(g)
    evals = [problem.evaluate(g) for g in genomes]
    k = 2 if getattr(problem, "robust", False) else len(problem.opponents)  # victory objectives, then spent
    order = sorted(range(len(genomes)), key=lambda i: (  # feasible, then soonest wins on average, then cheapest
        not evals[i].feasible, evals[i].total_violation, sum(evals[i].objectives[:k]), evals[i].objectives[k]))
    return [genomes[i] for i in order[:top_k]]


def choose_victory(problem: VictoryProblem, res: ParetoResult, preference: dict[str, float]) -> int:
    """Front point for a preference such as {"hard": 1, "spent": 0.3}; "victory" weighs every
    opponent alike."""
    names = victory_objectives(problem)
    weights = [preference.get(n, preference.get("victory", 0.0) if n not in ("spent", "survive") else 0.0)
               for n in names]
    return tchebycheff(res.evals, weights)


def pareto(problem: MissionProblem, **ga) -> ParetoResult:
    res = run_nsga2(problem, **ga)
    order = sorted(range(len(res.front)), key=lambda i: res.evals[i].objectives[1])
    return ParetoResult([res.front[i] for i in order], [res.evals[i] for i in order], res.history)


def choose(res: ParetoResult, preference: dict[str, float]) -> int:
    """Index of the front point for a preference such as {"win": 2, "frames": 1, "spent": 0}."""
    return tchebycheff(res.evals, [preference.get(k, 0.0) for k in OBJECTIVES])
