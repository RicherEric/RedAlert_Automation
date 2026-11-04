"""What happened in a recorded attack (monitor.py watch --log-objects during execute.py --attack).

    .venv\\Scripts\\python.exe battle_report.py logs\\battle_hard.jsonl [--from 2400]

For every unit of ours: where and when it died and which enemies were close by then; when each
unit reached the enemy base (how spread out the arrival was); what the enemy lost; and whether the
AI's army met us away from its base. Read-only: it only looks at the log.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict

from ra2opt import label, load_db

NEAR = 8.0        # cells: enemies this close to a dying unit are the likely killers
BASE_RADIUS = 15.0


def records(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "objects" in r:
                yield r


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log")
    ap.add_argument("--from", dest="start", type=int, default=0, help="只看這一幀之後（例如進攻開始）")
    ap.add_argument("--every", type=int, default=300, help="時間軸的取樣間隔（幀）")
    args = ap.parse_args()
    db = load_db()

    def combat(tid):
        t = db.technos.get(tid)
        return t is not None and t.kind != "building" and bool(t.combat_weapons) and not t.harvester

    me = ai = None
    base = None
    alive: dict[int, dict] = {}       # address -> last sighting of our unit
    deaths = []
    arrival: dict[int, tuple[int, str]] = {}
    enemy_seen: dict[int, dict] = {}
    enemy_lost = []
    timeline = []
    last_t = -args.every
    last_frame = 0
    for r in records(args.log):
        if ai is None:
            me = r["player_index"]
            ai = next(h["index"] for h in r["houses"] if h["index"] != me
                      and h["country"] not in ("Neutral", "Special") and not h["is_human"])
        frame = r["frame"]
        objs = [o for o in r["objects"] if o["on_map"] and o["health"] > 0]
        enemies = [o for o in objs if o["owner"] == ai]
        if base is None:
            pts = [(o["x"], o["y"]) for o in enemies if o["kind"] == "building"]
            if pts:
                base = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        if frame < args.start:
            continue
        ours = {o["address"]: o for o in objs if o["owner"] == me and combat(o["type_id"])}
        for addr, o in list(alive.items()):
            if addr not in ours:
                near = Counter(e["type_id"] for e in enemies if math.dist((e["x"], e["y"]), (o["x"], o["y"])) <= NEAR)
                to_base = math.dist((o["x"], o["y"]), base) if base else float("nan")
                deaths.append({"frame": o["frame"], "type": o["type_id"], "x": o["x"], "y": o["y"],
                               "to_base": to_base, "near": dict(near)})
                del alive[addr]
        for addr, o in ours.items():
            alive[addr] = {**o, "frame": frame}
            if base and addr not in arrival and math.dist((o["x"], o["y"]), base) <= BASE_RADIUS:
                arrival[addr] = (frame, o["type_id"])
        current = {e["address"]: e for e in enemies}
        for addr, e in list(enemy_seen.items()):
            if addr not in current:
                enemy_lost.append((e["frame"], e["type_id"], e["kind"]))
                del enemy_seen[addr]
        for addr, e in current.items():
            enemy_seen[addr] = {**e, "frame": frame}
        if frame - last_t >= args.every:
            last_t = frame
            units = [e for e in enemies if combat(e["type_id"])]
            out = [e for e in units if base and math.dist((e["x"], e["y"]), base) > BASE_RADIUS]
            d = sorted(math.dist((o["x"], o["y"]), base) for o in ours.values()) if base else []
            timeline.append((frame, len(ours), d[len(d) // 2] if d else None, len(units), len(out),
                             sum(1 for e in enemies if e["kind"] == "building")))
        last_frame = frame

    if base is None:
        sys.exit("記錄裡沒有 AI 建築")
    print(f"AI 基地中心 ({base[0]:.0f}, {base[1]:.0f})；記錄到第 {last_frame} 幀")
    print(f"\n時間軸（我方部隊、與敵方基地距離中位數、AI 部隊、離開基地的 AI 部隊、AI 建築）")
    for f, n, d, u, o, b in timeline:
        print(f"  {f:>6} 幀  我方 {n:>3}  距離 {'-' if d is None else f'{d:5.1f}'}  AI 部隊 {u:>3}（外出 {o:>2}）  AI 建築 {b:>3}")

    if arrival:
        by_type = defaultdict(list)
        for f, t in arrival.values():
            by_type[t].append(f)
        print("\n抵達敵方基地（距中心 15 格內）")
        for t, fs in by_type.items():
            fs.sort()
            print(f"  {label(db, t):<16} {len(fs):>3} 個  第 {fs[0]} – {fs[-1]} 幀（相差 {fs[-1] - fs[0]} 幀）")

    print(f"\n我方陣亡 {len(deaths)} 個")
    killers = Counter()
    where = Counter()
    for d in deaths:
        for t, n in d["near"].items():
            killers[t] += 1
        where["基地內" if d["to_base"] <= BASE_RADIUS else "路上"] += 1
    by_type = Counter(d["type"] for d in deaths)
    print("  依兵種：" + "，".join(f"{label(db, t)}×{n}" for t, n in by_type.most_common()))
    print("  陣亡地點：" + "，".join(f"{k} {n}" for k, n in where.items()))
    print("  陣亡時附近（8 格內）出現最多的敵方：" + "，".join(f"{label(db, t)} {n}" for t, n in killers.most_common(10)))
    for d in deaths:
        near = ", ".join(f"{t}×{n}" for t, n in d["near"].items()) or "（沒有）"
        print(f"    {d['frame']:>6} 幀  {d['type']:<5} 距基地 {d['to_base']:5.1f}  附近 {near}")

    lost = Counter((t, k) for _, t, k in enemy_lost)
    print(f"\n敵方損失 {len(enemy_lost)} 個：" + ("，".join(f"{label(db, t)}×{n}" for (t, _), n in lost.most_common())
                                           or "（沒有）"))


if __name__ == "__main__":
    main()
