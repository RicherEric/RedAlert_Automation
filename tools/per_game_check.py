"""How a plan fares against each recorded game's own AI timeline (not only the quantile composites).

    .venv\Scripts\python.exe tools\per_game_check.py scenarios\soviet_vs_hard_quantile.toml \
        --army APOC=3 BORIS=1 HTK=10 ZEP=3 --early HTNK=1 E2=1 HTK=2

Games recorded for less than --min-frames are skipped: Opponent.at() holds their last sample.
"""
from __future__ import annotations

import argparse
import glob
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ra2opt import load_calibration, load_db  # noqa: E402
from ra2tools.mission import victory_problem  # noqa: E402
from ra2tools.opponent import Opponent, games_from_logs  # noqa: E402

LOGS = ["logs/ai_hard.jsonl", "logs/battle_hard*.jsonl", "logs/observe_hard_[0-9].jsonl", "logs/challenge_hard_q.jsonl"]


def parse(items):
    out = {}
    for item in items or []:
        t, _, n = item.partition("=")
        out[t.upper()] = int(n or 1)
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--army", nargs="+", required=True)
    ap.add_argument("--early", nargs="*")
    ap.add_argument("--template", default="calibration/opponents/hard_americans_firestorm.json")
    ap.add_argument("--min-frames", type=int, default=16000)
    args = ap.parse_args()
    cfg = tomllib.loads(Path(args.scenario).read_text(encoding="utf-8"))
    db = load_db()
    paths = [p for pattern in LOGS for p in sorted(glob.glob(str(ROOT / pattern)))]
    games = games_from_logs(db, paths, Opponent.load(ROOT / args.template), args.min_frames)
    army, early = parse(args.army), parse(args.early)
    problem = victory_problem(db, cfg, load_calibration(cfg), games, model=str(ROOT / "models" / "build"))
    if early and not problem.early:
        sys.exit("情境沒有 [victory] early")
    ev = problem.evaluate(problem.genome_of(army, early))
    print(f"\n方案 {army}" + (f"，先蓋 {early}" if early else "") + f"：第 {ev.info['finish_frame']:.0f} 幀蓋好")
    won = 0
    for g in games:
        f = ev.info["vs"][g.name]
        why = ("獲勝 第 %.0f 幀" % f["victory_frame"]) if f["won"] else (
            f"輸：第 {f['lost_to_wave']} 幀的反攻" if f.get("lost_to_wave") else
            f"輸：第 {f['lost_to_raid']} 幀的空降" if f.get("lost_to_raid") else
            f"輸：基地第 {f['base_lost']:.0f} 幀失守" if f.get("base_lost") else f"輸：AI 剩 {f['b_left']:.0%}")
        won += bool(f["won"])
        print(f"  {g.name:<22} 反攻 {len(g.attacks)} 波、空降 {len(g.raids)} 次  {why}")
    print(f"\n模擬裡贏 {won}/{len(games)} 場")


if __name__ == "__main__":
    main()
