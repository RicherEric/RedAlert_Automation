"""Leave-one-out cross-validation of the robust search: for each recorded hard game, search against
the other games only, pick a plan, then score it on the held-out game. Seeds come only from the
scenario's hand-written [victory] seeds — earlier result files saw every game.

    .venv\Scripts\python.exe tools\robust_cv.py scenarios\soviet_vs_hard_quantile.toml
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
from tools.robust_search import LOGS, PREFER  # noqa: E402


def describe(db, army, early) -> str:
    return ((("先蓋 " + ", ".join(f"{label(db, t)}x{n}" for t, n in early.items()) + " → ") if early else "")
            + ", ".join(f"{label(db, t)}x{n}" for t, n in army.items()))


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
    cfg = {**cfg, "victory": {**v, "robust": True}}
    db = load_db()
    calibration = load_calibration(cfg)
    model = str(ROOT / "models" / "build")
    paths = [p for pattern in LOGS for p in sorted(glob.glob(str(ROOT / pattern)))]
    games = games_from_logs(db, paths, Opponent.load(ROOT / args.template))
    judge = victory_problem(db, cfg, calibration, games, model=model)
    print(f"留一交叉驗證：{len(games)} 場（{', '.join(g.name for g in games)}）")
    pop, gens = v.get("pop_size", 60), args.generations or v.get("generations", 40)
    folds = []
    for k, held in enumerate(games):
        train = [g for g in games if g is not held]
        problem = victory_problem(db, cfg, calibration, train, model=model)
        seeds = victory_seeds(problem, list(v.get("seeds", [])), v.get("top_k", pop // 4))
        t0 = time.time()
        res = pareto(problem, pop_size=pop, generations=gens, seed=args.seed, workers=v.get("workers", 1),
                     seeds=seeds)
        best = min(range(len(res.front)), key=lambda i: (res.evals[i].objectives[0], res.evals[i].objectives[1]))
        picks = {"最高勝率": best, "偏好": choose_victory(problem, res, PREFER)}
        fold = {"held_out": held.name, "seconds": time.time() - t0, "picks": {}}
        print(f"\n[{k + 1}/{len(games)}] 保留 {held.name}（搜尋 {time.time() - t0:.0f}s，前緣 {len(res.front)} 個）")
        for rule, i in picks.items():
            g = res.front[i]
            army, early = problem.army(g), problem.early_of(g)
            ev = judge.evaluate(judge.genome_of(army, early))
            vs = ev.info["vs"]
            won = vs[held.name]["won"]
            fold["picks"][rule] = {"army": army, "early": early, "train_rate": -res.evals[i].objectives[0],
                                   "held_out_won": bool(won), "held_out_frame": vs[held.name]["victory_frame"],
                                   "all_rate": sum(bool(f["won"]) for f in vs.values()) / len(vs),
                                   "spent": ev.objectives[2]}
            p = fold["picks"][rule]
            print(f"  {rule}：訓練 {p['train_rate']:.0%} → 保留那場 {'贏' if won else '輸'}"
                  f"  ${p['spent']:.0f}  {describe(db, army, early)}")
        folds.append(fold)
    print("\n=== 總結 ===")
    summary = {}
    for rule in ("最高勝率", "偏好"):
        train = sum(f["picks"][rule]["train_rate"] for f in folds) / len(folds)
        held = sum(f["picks"][rule]["held_out_won"] for f in folds)
        summary[rule] = {"train_rate": train, "held_out_wins": held, "folds": len(folds)}
        print(f"  {rule}：訓練平均勝率 {train:.0%}，沒看過的那場贏 {held}/{len(folds)}")
    out = ROOT / "results" / f"soviet_vs_hard_robust_cv_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"games": [g.name for g in games], "preference": PREFER, "generations": gens,
                               "summary": summary, "folds": folds}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"結果已存到 {out}")


if __name__ == "__main__":
    main()
