"""The paper's Table 3 for our search: NSGA-II started from TopK known armies vs all random.

    .venv\\Scripts\\python.exe tools\\topk_ablation.py scenarios\\soviet_vs_hard_quantile.toml --runs 3 --generations 20

For each run seed, both variants record per generation the soonest win against the last opponent
(the most conservative quantile) and the feasible share, so convergence speed can be compared.
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ra2opt import load_calibration, load_db  # noqa: E402
from ra2tools.mission import victory_problem, victory_seeds  # noqa: E402
from ra2tools.opponent import Opponent  # noqa: E402
from ra2tools.optimize import run_nsga2  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--generations", type=int, default=20)
    ap.add_argument("-o", "--out", default=str(ROOT / "results" / "topk_ablation.json"))
    args = ap.parse_args()
    cfg = tomllib.loads(Path(args.scenario).read_text(encoding="utf-8"))
    v = cfg["victory"]
    db = load_db()
    problem = victory_problem(db, cfg, load_calibration(cfg), [Opponent.load(ROOT / p) for p in v["opponents"]],
                              model=str(ROOT / "models" / "build"))
    k = len(problem.opponents) - 1  # objective index of the most conservative opponent
    known = list(v.get("seeds", []))
    patterns = v.get("seed_results", [])
    for pattern in [patterns] if isinstance(patterns, str) else patterns:
        for path in sorted(glob.glob(str(ROOT / pattern))):
            known += [{**r["army"], **({"early": r["early"]} if r.get("early") else {})}
                      for r in json.loads(Path(path).read_text(encoding="utf-8"))["front"] if r.get("feasible")]
    pop = v.get("pop_size", 60)
    seeds = victory_seeds(problem, known, v.get("top_k", pop // 4))
    out = {"scenario": args.scenario, "top_k": len(seeds), "runs": {}}
    for variant, init in (("random", []), ("topk", seeds)):
        for run in range(args.runs):
            curve = []

            def track(gen, front):
                wins = [e.objectives[k] for e in front if e.feasible and e.objectives[k] < problem.LOSS_FRAMES]
                curve.append(min(wins) if wins else None)
            run_nsga2(problem, pop_size=pop, generations=args.generations, seed=100 + run,
                      workers=v.get("workers", 1), seeds=init, on_generation=track)
            out["runs"].setdefault(variant, []).append(curve)
            first = next((g for g, c in enumerate(curve) if c), None)
            print(f"{variant:>6} run {run}: 第 0 代 {curve[0]}，第一次打贏 q80 在第 {first} 代，最後 {curve[-1]}")
    for variant, curves in out["runs"].items():
        finals = [c[-1] for c in curves if c[-1]]
        g0 = [c[0] for c in curves if c[0]]
        print(f"{variant:>6}: 第 0 代打得贏的 {len(g0)}/{len(curves)} 次；最後平均 "
              f"{statistics.mean(finals):.0f} ± {statistics.pstdev(finals):.0f} 幀（{len(finals)}/{len(curves)} 次有解）"
              if finals else f"{variant:>6}: 沒有解")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已存到 {args.out}")


if __name__ == "__main__":
    main()
