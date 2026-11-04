"""Train the build policy on GA-teacher data (stage 3) and compare it with the teacher.

    .venv\\Scripts\\python.exe train.py                    # datasets/build/*.npz -> models/build/
    .venv\\Scripts\\python.exe train.py --eval-only --tasks 30

Evaluation: masked accuracy against the teacher on held-out episodes, then full games in
BuildEnv on fresh tasks: the student alone vs the teacher (GA at every decision, no exploration),
compared on the weighted objective and on decision time. Tasks run in parallel, and the teacher's
games are cached in datasets/teacher_eval.json (they do not change between DAgger rounds).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import multiprocessing as mp
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
DATA = ROOT / "datasets" / "build"
OUT = ROOT / "models" / "build"


def load_data(pattern: str = "*.npz"):
    files = sorted(glob.glob(str(DATA / pattern)))
    if not files:
        sys.exit("datasets/build 沒有資料，請先執行 datagen.py")
    parts = [np.load(f) for f in files]
    data = {k: np.concatenate([p[k] for p in parts]) for k in parts[0].files}
    print(f"{len(files)} 個檔案，{len(data['action'])} 筆，{len(np.unique(data['episode']))} 局")
    return data


def masked_accuracy(probs: np.ndarray, classes: np.ndarray, mask: np.ndarray, y: np.ndarray) -> float:
    full = np.zeros((len(y), mask.shape[1]))
    full[:, classes] = probs
    full[~mask] = -1
    return float((full.argmax(1) == y).mean())


def train(data, rounds: int) -> dict:
    import lightgbm as lgb

    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    episodes = np.unique(data["episode"])
    rng = np.random.default_rng(0)
    val_eps = set(rng.choice(episodes, size=max(1, len(episodes) // 10), replace=False))
    val = np.array([e in val_eps for e in data["episode"]])
    x, y, mask = data["x"], data["action"], data["mask"].astype(bool)

    classes = np.unique(y)
    col = {c: i for i, c in enumerate(classes)}
    yc = np.array([col[c] for c in y])
    print(f"訓練 {(~val).sum()} 筆 / 驗證 {val.sum()} 筆，{len(classes)} 種動作")

    # Some actions are rare (one had 9 rows): without the hessian floor and L2 their leaf values blow
    # up after ~20 rounds, validation loss explodes and early stopping kept a 1-round model.
    params = {"objective": "multiclass", "num_class": len(classes), "learning_rate": 0.1,
              "num_leaves": 31, "min_data_in_leaf": 50, "min_sum_hessian_in_leaf": 1.0, "lambda_l2": 1.0,
              "feature_fraction": 0.8, "verbose": -1, "num_threads": 0}
    dtrain = lgb.Dataset(x[~val], yc[~val])
    dval = lgb.Dataset(x[val], yc[val], reference=dtrain)
    t = time.perf_counter()
    model = lgb.train(params, dtrain, rounds, valid_sets=[dval],
                      callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(50)])
    print(f"策略模型：{model.best_iteration} 輪，{time.perf_counter() - t:.0f} 秒")
    acc = masked_accuracy(model.predict(x[val]), classes, mask[val], y[val])
    base = masked_accuracy(np.ones((val.sum(), len(classes))), classes, mask[val], y[val])
    print(f"  驗證準確率（套用合法遮罩）{acc:.1%}（只選第一個合法動作：{base:.1%}）")

    vparams = {"objective": "regression", "learning_rate": 0.1, "num_leaves": 63, "min_data_in_leaf": 50,
               "lambda_l2": 1.0, "verbose": -1}
    # Frames still to go: the features carry no clock, so an absolute finish frame is not learnable.
    togo = data["finish"] - data["frame"]
    value = lgb.train(vparams, lgb.Dataset(x[~val], togo[~val]), rounds,
                      valid_sets=[lgb.Dataset(x[val], togo[val])],
                      callbacks=[lgb.early_stopping(20, verbose=False)])
    err = np.abs(value.predict(x[val]) - togo[val])
    print(f"價值模型（預測剩餘幀數）：驗證平均誤差 {err.mean():.0f} 幀，中位數 {np.median(err):.0f} 幀")

    OUT.mkdir(parents=True, exist_ok=True)
    model.save_model(str(OUT / "policy.txt"), num_iteration=model.best_iteration)
    value.save_model(str(OUT / "value_finish.txt"), num_iteration=value.best_iteration)
    (OUT / "meta.json").write_text(json.dumps({"vocab": meta["vocab"], "classes": classes.tolist(),
                                               "feature_version": meta.get("feature_version", 1),
                                               "rows": int(len(y)), "val_accuracy": acc},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"模型已存到 {OUT}")
    return {"rows": int(len(y)), "val_accuracy": acc, "baseline": base, "policy_rounds": model.best_iteration,
            "value_mae": float(err.mean()), "value_median": float(np.median(err))}


def play(env, task, choose) -> tuple[float, float, bool, float]:
    """(final frame, spent, finished, mean seconds per decision)."""
    obs = env.reset()
    spent_time, n = 0.0, 0
    while obs is not None:
        t = time.perf_counter()
        a = choose(env, obs)
        spent_time += time.perf_counter() - t
        n += 1
        obs = env.step(a)
    return env.state.frame, env.state.spent, env.finished(env.state), spent_time / max(n, 1)


_EVAL: dict = {}


def _eval_init() -> None:
    from datagen import load
    from ra2tools.policy import BuildPolicy
    db, cal = load()
    _EVAL.update(db=db, cal=cal, policy=BuildPolicy(OUT))


def _eval_task(job):
    """Play one task with the student (and the teacher unless cached)."""
    i, task, need_teacher = job
    from ra2tools.env import BuildEnv
    from ra2tools.teacher import Teacher
    db, policy = _EVAL["db"], _EVAL["policy"]
    c = replace(_EVAL["cal"], start_credits=task.start_credits)
    env = BuildEnv(db, task.country, task.targets, c, vocab=policy.vocab)
    before = policy.unstuck
    s = play(env, task, lambda e, o: policy.act(e, o, task.weights))
    s = (*s, policy.unstuck - before)
    t = None
    if need_teacher:
        teacher = Teacher(db, task, c, seed=i)

        def teach(e, o):
            a, _, _ = teacher.action(e, o)
            teacher.taken(e, a)
            return a

        env = BuildEnv(db, task.country, task.targets, c, vocab=policy.vocab)
        t = play(env, task, teach)
    return i, s, t


def evaluate(n_tasks: int, seed: int, workers: int = 0) -> dict:
    from datagen import load
    from ra2tools.teacher import sample_task

    db, cal = load()
    rng = random.Random(seed)
    tasks = [sample_task(db, rng) for _ in range(n_tasks)]
    cache_path = DATA.parent / "teacher_eval.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    cal_key = hashlib.md5(json.dumps(vars(cal), sort_keys=True, default=str).encode()).hexdigest()[:8]
    keys = [f"{cal_key}|{seed}|{i}|{t.country}|{sorted(t.targets.items())}|{t.weights}|{t.start_credits}"
            for i, t in enumerate(tasks)]
    jobs = [(i, t, keys[i] not in cache) for i, t in enumerate(tasks)]
    with mp.Pool(workers or min(n_tasks, max(1, mp.cpu_count() - 2)), initializer=_eval_init) as pool:
        results = sorted(pool.imap_unordered(_eval_task, jobs))
    for i, s, t in results:
        if t is not None:
            cache[keys[i]] = list(t)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")

    rows = []
    for i, s, _ in results:
        task, t = tasks[i], cache[keys[i]]
        w = task.weights

        def objective(frame, spent):
            return w["frames"] * frame + w["spent"] * spent

        gap = (objective(*s[:2]) - objective(*t[:2])) / objective(*t[:2]) if t[2] else float("nan")
        rows.append((task, s, t, gap))
        print(f"  {task.country:<12} {str(task.targets):<28} 學生 {s[0]:>6.0f} 幀 ${s[1]:>6.0f} "
              f"{'完成' if s[2] else '未完成'}{'*' if s[4] else ' '}| 老師 {t[0]:>6.0f} 幀 ${t[1]:>6.0f} | 差距 {gap:+.1%}")
    gaps = np.array([r[3] for r in rows if r[1][2] and r[2][2]])
    done = sum(r[1][2] for r in rows)
    teacher_done = sum(r[2][2] for r in rows)
    unstuck = sum(r[1][4] for r in rows)
    summary = {"tasks": len(rows), "student_done": int(done), "teacher_done": int(teacher_done),
               "unstuck": int(unstuck),
               "gap_median": float(np.median(gaps)), "gap_mean": float(gaps.mean()),
               "student_ms": float(np.mean([r[1][3] for r in rows]) * 1000),
               "teacher_ms": float(np.mean([r[2][3] for r in rows]) * 1000)}
    print(f"\n學生完成 {done}/{len(rows)} 局（老師 {teacher_done}）；兩者都完成的局，"
          f"加權目標差距中位數 {summary['gap_median']:+.1%}，平均 {summary['gap_mean']:+.1%}")
    print(f"每次決策：學生 {summary['student_ms']:.2f} ms，老師 {summary['teacher_ms']:.0f} ms")
    print(f"安全網介入 {unstuck} 次（{sum(r[1][4] > 0 for r in rows)} 局）")
    return summary


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Train / evaluate the build policy")
    ap.add_argument("--rounds", type=int, default=500)
    ap.add_argument("--tasks", type=int, default=20, help="評估用的新任務數")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--workers", type=int, default=0, help="評估用的平行程序數（0 = 自動）")
    ap.add_argument("--eval-only", action="store_true")
    args = ap.parse_args()
    if not args.eval_only:
        train(load_data(), args.rounds)
    evaluate(args.tasks, args.seed, args.workers)


if __name__ == "__main__":
    main()
