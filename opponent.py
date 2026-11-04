"""Measure what the AI fields over time from a live-game log, for ra2opt.py victory / execute.py --attack.

    .venv\\Scripts\\python.exe monitor.py watch --log-objects --duration 900 --log logs\\ai_game.jsonl
    .venv\\Scripts\\python.exe opponent.py extract logs\\ai_game.jsonl --name easy_americans_firestorm
    .venv\\Scripts\\python.exe opponent.py show calibration\\opponents\\easy_americans_firestorm.json
    .venv\\Scripts\\python.exe opponent.py quantile logs\\ai_hard.jsonl logs\\battle_hard*.jsonl --p 0.5 0.8 ^
        --template calibration\\opponents\\hard_americans_firestorm.json --name hard_q

quantile: the paper's threshold xi from several games (ra2tools.opponent.quantile), one opponent
per p, saved as <name><p*100>.json, with the xi table (what the AI has per 3-minute slot).
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from ra2opt import label, load_db
from ra2tools.opponent import SLOT, Opponent, from_log, quantile, threat

ROOT = Path(__file__).parent
OUT = ROOT / "calibration" / "opponents"


def show(db, opp: Opponent, step: int = 1500) -> None:
    print(f"{opp.name}：AI {opp.country} 對 {opp.player_country}，基地相距 {opp.base_distance:.1f} 格，"
          f"我方開局部隊 {opp.player_start_army}（來源 {opp.source}）")
    for f in range(0, opp.frames[-1] + 1, step):
        units, buildings = opp.at(f)
        value = sum(db.technos[t].cost * n for t, n in units.items())
        defence = {t: n for t, n in buildings.items() if db.technos[t].combat_weapons}
        print(f"  {f:>6} 幀  部隊 ${value:<6} 建築 {sum(buildings.values()):<3} 防禦 "
              + (", ".join(f"{label(db, t)}x{n}" for t, n in defence.items()) or "-")
              + "  |  " + ", ".join(f"{t}x{n}" for t, n in units.items()))


def xi_table(db, opp: Opponent, slot: int = SLOT) -> None:
    """Like the paper's Table 1: what the AI has at the end of each 3-minute slot."""
    print(f"  {'時段':<10} {'威脅$':>7}  部隊／防禦建築")
    for s in range(opp.frames[-1] // slot + 1):
        units, buildings = opp.at(min((s + 1) * slot - 1, opp.frames[-1]))
        armed = {t: n for t, n in buildings.items() if db.technos[t].combat_weapons}
        print(f"  {s * 3:>2}–{s * 3 + 3:<2} 分鐘 {threat(db, units, buildings):>7.0f}  "
              + (", ".join(f"{label(db, t)}x{n}" for t, n in {**units, **armed}.items()) or "-"))
    for w in opp.attacks:
        print(f"  反攻 第 {w['frame']} 幀（{w['frame'] / 1200:.1f} 分）：" + ", ".join(f"{label(db, t)}x{n}" for t, n in w["units"].items()))
    for w in opp.raids:
        print(f"  空降斬首 第 {w['frame']} 幀（{w['frame'] / 1200:.1f} 分）：" + ", ".join(f"{label(db, t)}x{n}" for t, n in w["units"].items()))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="AI opponent timelines")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("log")
    e.add_argument("--name", required=True)
    e.add_argument("--every", type=int, default=300, help="每幾幀取樣一次")
    s = sub.add_parser("show")
    s.add_argument("path")
    q = sub.add_parser("quantile")
    q.add_argument("logs", nargs="+", help="monitor.py --log-objects 紀錄（可用萬用字元）")
    q.add_argument("--p", type=float, nargs="+", default=[0.5, 0.8])
    q.add_argument("--template", required=True, help="同難度的對手 json：反應、補兵速度、開局部隊從這裡來")
    q.add_argument("--name", required=True, help="輸出檔名前綴，例如 hard_q → hard_q50.json")
    args = ap.parse_args()
    db = load_db()
    if args.cmd == "extract":
        opp = from_log(db, Path(args.log), args.name, args.every)
        path = OUT / f"{args.name}.json"
        opp.save(path)
        show(db, opp)
        print(f"已存到 {path}")
    elif args.cmd == "quantile":
        paths = sorted({f for pat in args.logs for f in glob.glob(pat)})
        games = []
        for path in paths:
            g = from_log(db, Path(path), Path(path).stem, until_attacked=True)
            games.append(g)
            print(f"{path}：到第 {g.frames[-1]} 幀，反攻 {len(g.attacks)} 波")
        template = Opponent.load(Path(args.template))
        for p in args.p:
            opp = quantile(db, games, p, f"{args.name}{round(p * 100)}", template)
            path = OUT / f"{opp.name}.json"
            opp.save(path)
            print(f"\n== p = {p}（贏過 {p:.0%} 的對局）：{len(games)} 場 → {path} ==")
            xi_table(db, opp)
    else:
        show(db, Opponent.load(Path(args.path)))


if __name__ == "__main__":
    main()
