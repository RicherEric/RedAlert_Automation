"""Generate imitation-learning data: GA teacher labels on BuildEnv decisions (stage 2).

    .venv\\Scripts\\python.exe datagen.py --minutes 30 --workers 16      # -> datasets/build/*.npz
    .venv\\Scripts\\python.exe datagen.py --episodes 20 --workers 1      # quick check
    .venv\\Scripts\\python.exe datagen.py --student models\\build --beta 0.5   # one DAgger round

With --student the trained policy plays (the teacher's move with probability beta) and the
teacher labels every state the student reaches (see dagger.py). Each shard holds per-decision
rows: x (features incl. objective weights), mask, action (teacher), finish/spent (the teacher
plan's predicted outcome from that state), plus the episode id.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
import tomllib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
OUT = ROOT / "datasets" / "build"


def load():
    from ra2opt import load_db
    from ra2tools.params import Calibration
    db = load_db()
    path = ROOT / "calibration" / "calibration.toml"
    cal = Calibration.from_dict(tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {})
    return db, cal.as_bot()


def worker(wid: int, seed: int, deadline: float, episodes: int, shard_rows: int, queue,
           student: str | None = None, beta: float = 1.0, tag: str = "w") -> None:
    from ra2tools.teacher import episode, global_vocab, sample_task
    db, cal = load()
    vocab = global_vocab(db, cal)
    rng = random.Random(seed * 1000 + wid)
    actor, explore = None, 0.15
    if student:
        from ra2tools.policy import BuildPolicy
        policy = BuildPolicy(Path(student))
        explore = 0.05

        def actor(env, obs, teacher_action):
            if rng.random() < beta:
                return teacher_action
            return policy.act(env, obs, task.weights)
    buf: dict[str, list] = {}
    shard = done = 0

    def flush():
        nonlocal shard, buf
        if not buf:
            return
        path = OUT / f"{tag}{seed}_{wid:02d}_{shard:04d}.npz"
        np.savez_compressed(path, **{k: np.concatenate(v) for k, v in buf.items()})
        shard += 1
        buf = {}

    while time.time() < deadline and (episodes <= 0 or done < episodes):
        task = sample_task(db, rng)
        ep = episode(db, task, cal, vocab, rng, explore=explore, actor=actor)
        n = len(ep["action"])
        if n:
            ep["episode"] = np.full(n, seed * 1_000_000 + wid * 10_000 + done)
            for k in ("final_frame", "final_spent", "finished"):
                ep[k] = np.full(n, ep[k])
            for k, v in ep.items():
                buf.setdefault(k, []).append(v)
        done += 1
        queue.put(n)
        if sum(len(v) for v in buf.get("action", [])) >= shard_rows:
            flush()
    flush()


def generate(minutes: float, workers: int, seed: int, episodes: int = 0, shard_rows: int = 5000,
             student: str | None = None, beta: float = 1.0, tag: str = "w") -> tuple[int, int]:
    """Run the workers; returns (episodes, rows)."""
    OUT.mkdir(parents=True, exist_ok=True)
    from ra2tools.env import FEATURE_VERSION
    from ra2tools.teacher import global_vocab
    db, cal = load()
    meta_path = OUT / "meta.json"
    if meta_path.exists() and any(OUT.glob("*.npz")):
        old = json.loads(meta_path.read_text(encoding="utf-8")).get("feature_version", 1)
        if old != FEATURE_VERSION:
            sys.exit(f"{OUT} holds feature version {old} rows, code is at {FEATURE_VERSION}: move them away first")
    meta = {"vocab": global_vocab(db, cal), "categories": ["building", "vehicle", "infantry", "aircraft"],
            "features": "ra2tools.env.features", "feature_version": FEATURE_VERSION, "calibration": vars(cal)}
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    deadline = time.time() + minutes * 60
    queue = mp.Queue()
    procs = [mp.Process(target=worker, args=(i, seed, deadline, episodes, shard_rows, queue, student, beta, tag))
             for i in range(workers)]
    for p in procs:
        p.start()
    rows = eps = 0
    start = time.time()
    while any(p.is_alive() for p in procs) or not queue.empty():
        try:
            rows += queue.get(timeout=1)
            eps += 1
        except Exception:
            continue
        if eps % 50 == 0:
            el = time.time() - start
            print(f"  {eps} 局，{rows} 筆，{rows / el:.0f} 筆/秒", flush=True)
    for p in procs:
        p.join()
    print(f"完成：{eps} 局，{rows} 筆 → {OUT}")
    return eps, rows


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="GA-teacher data for the build policy")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--episodes", type=int, default=0, help="每個 worker 最多幾局（0 = 只看時間）")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument("--shard-rows", type=int, default=5000)
    ap.add_argument("--student", help="DAgger：由這個模型目錄的策略下棋，老師只負責標註")
    ap.add_argument("--beta", type=float, default=0.5, help="DAgger：每步改走老師動作的機率")
    args = ap.parse_args()
    generate(args.minutes, args.workers, args.seed, args.episodes, args.shard_rows,
             args.student, args.beta, "d" if args.student else "w")


if __name__ == "__main__":
    main()
