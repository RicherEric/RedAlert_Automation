"""DAgger rounds for the build policy (stage 4): the student plays, the GA teacher labels.

    .venv\\Scripts\\python.exe dagger.py --rounds 3 --minutes 15      # after datagen.py (teacher-only data)

Imitation learning only sees states the teacher reaches, so a student that strays once lands in
unfamiliar states and its mistakes compound (it waits forever, or builds things it does not need).
Each round lets the current student play (taking the teacher's move with probability beta, which
halves every round), has the teacher label every state reached, adds those rows to the dataset,
retrains on everything, and evaluates on the same fixed tasks. Progress goes to models/build/dagger.json.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time

import datagen
import train


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="DAgger rounds for the build policy")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=15, help="每輪收集資料的時間")
    ap.add_argument("--beta", type=float, default=0.5, help="第一輪走老師動作的機率，之後每輪減半")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--tasks", type=int, default=20, help="評估用的固定任務數")
    ap.add_argument("--seed", type=int, default=12345, help="評估任務的種子（每輪相同）")
    ap.add_argument("--train-rounds", type=int, default=500)
    args = ap.parse_args()
    log_path = train.OUT / "dagger.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    if not log:
        print("== 第 0 輪：只用老師資料（w*）訓練並評估 ==")
        metrics = train.train(train.load_data("w*.npz"), args.train_rounds)
        log.append({"round": 0, "train": metrics, "eval": train.evaluate(args.tasks, args.seed, args.workers)})
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    first = log[-1]["round"] + 1
    for r in range(first, first + args.rounds):
        beta = args.beta * 0.5 ** (r - 1)
        print(f"\n== 第 {r} 輪：學生下棋（β={beta:.3f}），老師標註 {args.minutes:g} 分鐘 ==", flush=True)
        eps, rows = datagen.generate(args.minutes, args.workers, seed=int(time.time()) % 100000,
                                     student=str(train.OUT), beta=beta, tag=f"d{r}_")
        print(f"\n== 第 {r} 輪：用全部資料重新訓練 ==", flush=True)
        metrics = train.train(train.load_data(), args.train_rounds)
        print(f"\n== 第 {r} 輪：評估 ==", flush=True)
        summary = train.evaluate(args.tasks, args.seed, args.workers)
        log.append({"round": r, "beta": beta, "episodes": eps, "new_rows": rows, "train": metrics,
                    "eval": summary})
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n輪  完成    差距中位數  差距平均  驗證準確率")
    for e in log:
        ev, tr = e["eval"], e.get("train", {})
        acc = f"{tr['val_accuracy']:.1%}" if tr else "-"
        print(f"{e['round']:>2}  {ev['student_done']:>2}/{ev['tasks']:<3} {ev['gap_median']:>+9.1%} "
              f"{ev['gap_mean']:>+9.1%}  {acc:>8}")


if __name__ == "__main__":
    main()
