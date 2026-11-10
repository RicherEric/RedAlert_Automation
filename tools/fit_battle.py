"""Replay every live attack wave in the battle simulator and see which side-B settings tell the
won waves from the lost ones (series_v6-v10, 2026-11-09).

Each wave: our army near the gather point at the frame it gathered, against the AI's units and
buildings on the map at that frame, both read from the game log. Won = the game was won by that
wave; lost = the log says the wave was beaten, or the game ended without a win.

    .venv\Scripts\python.exe tools\fit_battle.py scenarios\soviet_vs_hard_quantile.toml
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import re
import sys
import tomllib
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ra2opt import load_calibration, load_db  # noqa: E402
from ra2tools.assault import NOT_ARMY  # noqa: E402
from ra2tools.combat import Battle  # noqa: E402
from ra2tools.problems import CombatSettings  # noqa: E402

LOGS = ["logs/series_v6_2*_[0-9]*.txt", "logs/series_v7_2*_[0-9]*.txt", "logs/series_v8_2*_[0-9]*.txt",
        "logs/series_v10_2*_[0-9]*.txt", "logs/series_v11_2*_[0-9]*.txt", "logs/series_v12_2*_[0-9]*.txt"]
GATHER = re.compile(r"(\d+) 幀\s+集結完成（集結點 \((\d+), (\d+)\)）")
LOST = re.compile(r"(\d+) 幀\s+這一波打輸了")
ARMY_RADIUS = 14.0  # cells around the gather point that count as the wave
TOWERS = ("ATESLA", "GAPILL", "NASAM", "GASAM")


def waves(path: Path) -> list[dict]:
    """(gather frame, gather point, won) of each wave in a series game's text log."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if "中止" in text or "使用者要求" in text:
        return []
    won = re.search(r"第 \d+ 幀獲勝", text) is not None
    out = []
    for m in GATHER.finditer(text):
        out.append({"frame": int(m.group(1)), "at": (int(m.group(2)), int(m.group(3))), "won": False})
    if not out:
        return []
    lost = {int(m.group(1)) for m in LOST.finditer(text)}
    for i, w in enumerate(out):
        nxt = out[i + 1]["frame"] if i + 1 < len(out) else math.inf
        beaten = any(w["frame"] <= f < nxt for f in lost)
        w["won"] = won and not beaten and i == len(out) - 1
    return out


def snapshot(db, log: Path, frame: int, at: tuple[int, int]) -> tuple[dict, dict]:
    with open(log, encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            if "objects" in s and s["frame"] >= frame:
                break
    me = s["player_index"]
    ai = next(h["index"] for h in s["houses"] if not h["is_human"] and h["country"] not in ("Neutral", "Special"))
    ok = lambda o: o["on_map"] and o["health"] > 0 and o["type_id"] in db.technos
    ours = Counter(o["type_id"] for o in s["objects"] if o["owner"] == me and ok(o)
                   and db.technos[o["type_id"]].kind != "building" and db.technos[o["type_id"]].combat_weapons
                   and o["type_id"] not in NOT_ARMY and o["type_id"] != "HARV"
                   and math.dist((o["x"], o["y"]), at) <= ARMY_RADIUS)
    theirs = Counter(o["type_id"] for o in s["objects"] if o["owner"] == ai and ok(o) and o["type_id"] != "CMIN")
    return dict(ours), dict(theirs)


def run(job) -> float:
    db, cal, c, ours, theirs, bd, rate = job
    b = Battle(db, ours, theirs, cal, c.distance, c.spacing, rebuild=True, b_damage=bd, replace_rate=rate,
               base_spacing=c.base_spacing)
    return b.run(c.dt, 20000, c.crush, c.kite, c.focus).score


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--b-damage", type=float, nargs="+", default=[1.0, 1.5, 2.0, 2.5, 3.0])
    ap.add_argument("--replace-rate", type=float, default=3.0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    cfg = tomllib.loads(Path(args.scenario).read_text(encoding="utf-8"))
    db, cal = load_db(), load_calibration(cfg)
    c = CombatSettings(**{**cfg.get("combat", {}), **cfg.get("victory", {}).get("combat", {})})
    rows = []
    for pattern in LOGS:
        for txt in sorted(glob.glob(str(ROOT / pattern))):
            txt = Path(txt)
            log = txt.with_suffix(".jsonl")
            if not log.exists() or not re.search(r"_\d+$", txt.stem):
                continue
            for w in waves(txt):
                ours, theirs = snapshot(db, log, w["frame"], w["at"])
                rows.append({"game": txt.stem, **w, "ours": ours, "theirs": theirs})
    jobs = [(db, cal, c, r["ours"], r["theirs"], bd, args.replace_rate) for r in rows for bd in args.b_damage]
    with ProcessPoolExecutor(args.workers) as pool:
        scores = list(pool.map(run, jobs, chunksize=1))
    k = len(args.b_damage)
    value = lambda a: sum(db.technos[t].cost * n for t, n in a.items())
    print(f"{'場次':<24}{'集結幀':>7} {'實際':<4}{'我方$':>7}{'AI單位$':>8} 光稜 碉堡  "
          + "  ".join(f"bd{bd:<4}" for bd in args.b_damage))
    right = [0] * k
    for i, r in enumerate(rows):
        sc = scores[i * k:(i + 1) * k]
        for j, s in enumerate(sc):
            right[j] += (s > 0) == r["won"]
        units = {t: n for t, n in r["theirs"].items() if db.technos[t].kind != "building"}
        print(f"{r['game']:<24}{r['frame']:>7} {'贏' if r['won'] else '輸':<4}{value(r['ours']):>7.0f}"
              f"{value(units):>8.0f} {r['theirs'].get('ATESLA', 0):>4} {r['theirs'].get('GAPILL', 0):>4}  "
              + "  ".join(f"{s:+.2f} " for s in sc))
    print("判對：" + "  ".join(f"bd{bd}={n}/{len(rows)}" for bd, n in zip(args.b_damage, right)))


if __name__ == "__main__":
    main()
