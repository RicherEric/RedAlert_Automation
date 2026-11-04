"""Constrained genetic algorithms: one objective (run_ga) or a Pareto front (run_nsga2).

Constraint handling follows Deb's feasibility rules: a feasible solution always beats an
infeasible one, two infeasible solutions are compared by total violation, and two feasible
ones by objective. Each constraint reports a non-negative violation (0 = satisfied), so any
number of conditions can be combined without hand-tuned penalty weights.
"""
from __future__ import annotations

import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class Evaluation:
    objective: float  # minimised
    violations: dict[str, float] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    objectives: tuple[float, ...] = ()  # all minimised, for run_nsga2

    @property
    def total_violation(self) -> float:
        return sum(self.violations.values())

    @property
    def feasible(self) -> bool:
        return self.total_violation <= 1e-9

    def dominates(self, other: "Evaluation") -> bool:
        """Constrained domination (Deb): feasible beats infeasible, less violation beats more,
        and a feasible one is no worse in any objective and better in at least one."""
        if self.feasible != other.feasible:
            return self.feasible
        if not self.feasible:
            return self.total_violation < other.total_violation
        pairs = list(zip(self.objectives, other.objectives))
        return all(a <= b for a, b in pairs) and any(a < b for a, b in pairs)

    def better_than(self, other: "Evaluation") -> bool:
        if self.feasible != other.feasible:
            return self.feasible
        if not self.feasible:
            return self.total_violation < other.total_violation
        return self.objective < other.objective


class Problem(Protocol):
    def random_genome(self, rng: random.Random) -> Any: ...
    def mutate(self, genome: Any, rng: random.Random) -> Any: ...
    def crossover(self, a: Any, b: Any, rng: random.Random) -> Any: ...
    def evaluate(self, genome: Any) -> Evaluation: ...
    def key(self, genome: Any) -> Any: ...


_worker_problem: Problem | None = None


def _init_worker(problem: Problem) -> None:
    global _worker_problem
    _worker_problem = problem


def _evaluate_in_worker(genome: Any) -> Evaluation:
    return _worker_problem.evaluate(genome)


@dataclass
class GAResult:
    best: Any
    best_eval: Evaluation
    history: list[dict]  # per generation: best objective, feasible share, ...


def run_ga(problem: Problem, pop_size: int = 40, generations: int = 40, seed: int = 0,
           elite: int = 2, tournament: int = 3, crossover_rate: float = 0.7,
           workers: int = 1, seeds: list[Any] | None = None,
           on_generation: Callable[[int, Any, Evaluation], None] | None = None) -> GAResult:
    rng = random.Random(seed)
    cache: dict[Any, Evaluation] = {}
    pool = ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(problem,)) if workers > 1 else None

    def evaluate_all(genomes: list) -> list[Evaluation]:
        todo = {}
        for g in genomes:
            k = problem.key(g)
            if k not in cache and k not in todo:
                todo[k] = g
        if todo:
            items = list(todo.items())
            results = pool.map(_evaluate_in_worker, [g for _, g in items], chunksize=4) if pool \
                else map(problem.evaluate, [g for _, g in items])
            for (k, _), ev in zip(items, results):
                cache[k] = ev
        return [cache[problem.key(g)] for g in genomes]

    def pick(pop: list, evals: list[Evaluation]) -> Any:
        best = rng.randrange(len(pop))
        for _ in range(tournament - 1):
            j = rng.randrange(len(pop))
            if evals[j].better_than(evals[best]):
                best = j
        return pop[best]

    try:
        pop = list(seeds or [])[:pop_size]
        pop += [problem.random_genome(rng) for _ in range(pop_size - len(pop))]
        evals = evaluate_all(pop)
        history = []
        for gen in range(generations + 1):
            order = sorted(range(len(pop)), key=lambda i: _rank_key(evals[i]))
            pop = [pop[i] for i in order]
            evals = [evals[i] for i in order]
            history.append({
                "generation": gen,
                "best_objective": evals[0].objective,
                "best_violation": evals[0].total_violation,
                "feasible_share": sum(e.feasible for e in evals) / len(evals),
            })
            if on_generation:
                on_generation(gen, pop[0], evals[0])
            if gen == generations:
                break
            children = pop[:elite]
            while len(children) < pop_size:
                a = pick(pop, evals)
                child = problem.crossover(a, pick(pop, evals), rng) if rng.random() < crossover_rate else a
                children.append(problem.mutate(child, rng))
            pop = children
            evals = evaluate_all(pop)
        return GAResult(pop[0], evals[0], history)
    finally:
        if pool:
            pool.shutdown()


def _rank_key(ev: Evaluation) -> tuple:
    return (0, ev.objective) if ev.feasible else (1, ev.total_violation)


# ------------------------------------------------------------------------------ NSGA-II
@dataclass
class ParetoResult:
    front: list[Any]               # non-dominated genomes (feasible ones whenever any exist)
    evals: list[Evaluation]
    history: list[dict]


def fronts(evals: list[Evaluation]) -> list[list[int]]:
    """Fast non-dominated sort: indices grouped by rank, best first."""
    n = len(evals)
    beaten_by = [0] * n
    beats: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if evals[i].dominates(evals[j]):
                beats[i].append(j)
                beaten_by[j] += 1
            elif evals[j].dominates(evals[i]):
                beats[j].append(i)
                beaten_by[i] += 1
    out = []
    current = [i for i in range(n) if beaten_by[i] == 0]
    while current:
        out.append(current)
        nxt = []
        for i in current:
            for j in beats[i]:
                beaten_by[j] -= 1
                if beaten_by[j] == 0:
                    nxt.append(j)
        current = nxt
    return out


def crowding(evals: list[Evaluation], front: list[int]) -> dict[int, float]:
    """Crowding distance within one front (boundary points are infinite, so the extremes survive)."""
    if len(front) < 3 or not evals[front[0]].objectives:
        return {i: float("inf") for i in front}
    dist = {i: 0.0 for i in front}
    for m in range(len(evals[front[0]].objectives)):
        order = sorted(front, key=lambda i: evals[i].objectives[m])
        lo, hi = evals[order[0]].objectives[m], evals[order[-1]].objectives[m]
        dist[order[0]] = dist[order[-1]] = float("inf")
        if hi - lo <= 0:
            continue
        for k in range(1, len(order) - 1):
            dist[order[k]] += (evals[order[k + 1]].objectives[m] - evals[order[k - 1]].objectives[m]) / (hi - lo)
    return dist


def run_nsga2(problem: Problem, pop_size: int = 60, generations: int = 40, seed: int = 0,
              tournament: int = 2, crossover_rate: float = 0.7, workers: int = 1,
              seeds: list[Any] | None = None,
              on_generation: Callable[[int, list[Evaluation]], None] | None = None) -> ParetoResult:
    """NSGA-II: problem.evaluate must fill Evaluation.objectives (all minimised). Constraints use
    the same violation dicts as run_ga, through constrained domination."""
    rng = random.Random(seed)
    cache: dict[Any, Evaluation] = {}
    pool = ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(problem,)) if workers > 1 else None

    def evaluate_all(genomes: list) -> list[Evaluation]:
        todo = {problem.key(g): g for g in genomes if problem.key(g) not in cache}
        if todo:
            items = list(todo.items())
            results = pool.map(_evaluate_in_worker, [g for _, g in items], chunksize=4) if pool \
                else map(problem.evaluate, [g for _, g in items])
            for (k, _), ev in zip(items, results):
                cache[k] = ev
        return [cache[problem.key(g)] for g in genomes]

    def unique(genomes: list) -> list:
        seen, out = set(), []
        for g in genomes:
            if problem.key(g) not in seen:
                seen.add(problem.key(g))
                out.append(g)
        return out

    def select(pop: list, evals: list[Evaluation]) -> tuple[list, list[Evaluation], list[tuple]]:
        """Keep pop_size by (rank, -crowding); returns the survivors and their sort keys."""
        rank_key: dict[int, tuple] = {}
        for r, front in enumerate(fronts(evals)):
            cd = crowding(evals, front)
            for i in front:
                rank_key[i] = (r, -cd[i])
        keep = sorted(range(len(pop)), key=rank_key.__getitem__)[:pop_size]
        return [pop[i] for i in keep], [evals[i] for i in keep], [rank_key[i] for i in keep]

    try:
        pop = unique(list(seeds or []) + [problem.random_genome(rng) for _ in range(pop_size)])[:pop_size]
        pop, evals, keys = select(pop, evaluate_all(pop))
        history = []
        for gen in range(generations + 1):
            first = [e for e, k in zip(evals, keys) if k[0] == 0]
            history.append({"generation": gen, "front_size": len(first),
                            "feasible_share": sum(e.feasible for e in evals) / len(evals)})
            if on_generation:
                on_generation(gen, first)
            if gen == generations:
                break

            def pick() -> Any:
                best = rng.randrange(len(pop))
                for _ in range(tournament - 1):
                    j = rng.randrange(len(pop))
                    if keys[j] < keys[best]:
                        best = j
                return pop[best]

            children = []
            for _ in range(pop_size * 3):  # duplicates are dropped later, so allow extra draws
                if len(children) >= pop_size:
                    break
                a = pick()
                child = problem.crossover(a, pick(), rng) if rng.random() < crossover_rate else a
                children.append(problem.mutate(child, rng))
            union = unique(pop + children)
            pop, evals, keys = select(union, evaluate_all(union))
        best = [i for i, k in enumerate(keys) if k[0] == 0]
        return ParetoResult([pop[i] for i in best], [evals[i] for i in best], history)
    finally:
        if pool:
            pool.shutdown()


def tchebycheff(evals: list[Evaluation], weights: list[float], ideal: list[float] | None = None,
                nadir: list[float] | None = None) -> int:
    """Index of the front point a preference picks: min over points of max_m w_m * normalised
    objective m (weighted sum as tie-break). Unlike a weighted sum alone it can pick points on
    concave parts of the front."""
    objs = [e.objectives for e in evals]
    m = len(objs[0])
    lo = ideal or [min(o[k] for o in objs) for k in range(m)]
    hi = nadir or [max(o[k] for o in objs) for k in range(m)]
    span = [max(h - l, 1e-9) for l, h in zip(lo, hi)]

    def norm(i: int) -> list[float]:
        return [w * (objs[i][k] - lo[k]) / span[k] for k, w in enumerate(weights)]

    return min(range(len(objs)), key=lambda i: (max(norm(i)), sum(norm(i))))
