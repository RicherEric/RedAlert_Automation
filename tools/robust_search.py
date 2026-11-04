"""Multi-objective search against every recorded hard-AI game at once (VictoryProblem robust mode):
win rate over the games, mean victory frame, spent, army left. TopK seeds as in ra2opt victory.

    .venv\Scripts\python.exe tools\robust_search.py scenarios\soviet_vs_hard_quantile.toml
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ra2opt import label, load_calibration, load_db  # noqa: E402
from ra2tools.mission import choose_victory, pareto, victory_problem, victory_seeds  # noqa: E402
from ra2tools.opponent import Opponent, games_from_logs  # noqa: E402

LOGS = ["logs/ai_hard.jsonl", "logs/battle_hard*.jsonl", "logs/observe_hard_[0-9].jsonl", "logs/challenge_hard_q.jsonl"]
PREFER = {"win_rate": 1.0, "frames": 0.4, "spent": 0.2, "survive": 0.2}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--template", default="calibration/opponents/hard_americans_firestorm.json")
    ap.add_argument("--generations", type=int)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    cfg = tomllib.loads(Path(args.scenario).read_text(encoding="utf-8"))
    v = cfg["victory"]
    db = load_db()
    paths = [p for pattern in LOGS for p in sorted(glob.glob(str(ROOT / pattern)))]
    games = games_from_logs(db, paths, Opponent.load(ROOT / args.template))
    print(f"對手：{len(games)} 場真實對局（{', '.join(g.name for g in games)}）")
    cfg = {**cfg, "victory": {**v, "robust": True}}
    problem = victory_problem(db, cfg, load_calibration(cfg), games, model=str(ROOT / "models" / "build"))
    known = list(v.get("seeds", []))
    patterns = v.get("seed_results", [])
    for pattern in [patterns] if isinstance(patterns, str) else patterns + ["results/soviet_vs_hard_robust_*.json"]:
        for path in sorted(glob.glob(str(ROOT / pattern))):
            known += [{**r["army"], **({"early": r["early"]} if r.get("early") else {})}
                      for r in json.loads(Path(path).read_text(encoding="utf-8"))["front"] if r.get("feasible")]
    pop = v.get("pop_size", 60)
    seeds = victory_seeds(problem, known, v.get("top_k", pop // 4))
    print(f"初始族群：{len(known)} 個已知方案中挑最好的 {len(seeds)} 個，其餘隨機")
    t0 = time.time()
    res = pareto(problem, pop_size=pop, generations=args.generations or v.get("generations", 40), seed=args.seed,
                 workers=v.get("workers", 1), seeds=seeds,
                 on_generation=lambda g, f: print(f"  第 {g:>2} 代  前緣 {len(f)} 個  {time.time() - t0:5.0f}s"))
    pick = choose_victory(problem, res, PREFER)
    rows = []
    order = sorted(range(len(res.front)), key=lambda i: (res.evals[i].objectives[0], res.evals[i].objectives[1]))
    print(f"\n=== 前緣 {len(res.front)} 個（依勝率排序）===\n   #  勝率  平均幀    花費  剩下   蓋好  部隊")
    for i in order:
        g, ev = res.front[i], res.evals[i]
        army, early = problem.army(g), problem.early_of(g)
        rate, mean, spent, left = -ev.objectives[0], ev.objectives[1], ev.objectives[2], -ev.objectives[3]
        print(f"{'←' if i == pick else ' '} {i:>2} {rate:>5.0%} {mean:>7.0f} {spent:>7.0f} {left:>4.0%} "
              f"{ev.info['finish_frame']:>6.0f}  "
              + (("先蓋 " + ", ".join(f"{label(db, t)}x{n}" for t, n in early.items()) + " → ") if early else "")
              + ", ".join(f"{label(db, t)}x{n}" for t, n in army.items()))
        rows.append({"army": army, "early": early, "win_rate": rate, "mean_frame": mean, "spent": spent,
                     "survive": left, "ready": ev.info["finish_frame"], "feasible": ev.feasible,
                     "games": {o.name: ev.info["vs"][o.name]["victory_frame"] for o in games}})
    c = rows[order.index(pick)]
    print(f"\n偏好 {PREFER} → 第 {pick} 個：勝率 {c['win_rate']:.0%}，花費 ${c['spent']:.0f}")
    for name, w in c["games"].items():
        print(f"  {name:<20} " + (f"第 {w:.0f} 幀獲勝" if w else "輸"))
    print("實機執行：execute.py --attack --targets " + " ".join(f"{t}={n}" for t, n in c["army"].items())
          + (" --early " + " ".join(f"{t}={n}" for t, n in c["early"].items()) if c["early"] else ""))
    out = ROOT / "results" / f"soviet_vs_hard_robust_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"games": [g.name for g in games], "preference": PREFER, "front": rows,
                               "chosen": order.index(pick), "history": res.history}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"結果已存到 {out}")


if __name__ == "__main__":
    main()
