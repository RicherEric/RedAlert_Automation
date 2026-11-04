"""Animate a recorded battle as a top-down video (for talks and for seeing what went wrong).

    .venv\\Scripts\\python.exe battle_movie.py logs\\battle_medium_rush2.jsonl --from 2100 --to 5700 ^
        --execute logs\\battle_medium_rush2_execute.json --title "第 6 場：磁爆步兵快攻 vs 中等 AI" -o out.mp4

Reads a monitor.py watch --log-objects log. Our units are red, the AI's blue, neutral city
buildings grey; a cross marks where a unit died and fades out. The right panel shows game time,
army counts and the phase taken from execute.py's log (built, gathered, won/lost). Needs ffmpeg.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FFMpegWriter  # noqa: E402

from ra2opt import load_db  # noqa: E402

plt.rcParams["font.family"] = ["Microsoft JhengHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

FPS = 20          # game frames per second at speed setting 3
BG, PANEL, TEXT, MUTED = "#0f1626", "#16213a", "#e8ecf3", "#8d99ae"
OURS, THEIRS, NEUTRAL = "#ff5a4e", "#4ea3ff", "#59606e"
FADE = 60         # game frames a death cross stays visible


def load(path: Path, start: int, end: int) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "objects" not in r or not r.get("houses"):
                continue
            if r["frame"] > end:
                break
            if r["frame"] >= start:
                out.append(r)
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log")
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--to", dest="end", type=int, default=10**9)
    ap.add_argument("--step", type=int, default=10, help="game frames per video frame")
    ap.add_argument("--fps", type=int, default=20, help="video frames per second")
    ap.add_argument("--execute", help="execute.py --log JSON, for the phase labels")
    ap.add_argument("--title", default="")
    ap.add_argument("-o", "--out", default="battle.mp4")
    args = ap.parse_args()

    db = load_db()
    recs = load(Path(args.log), args.start, args.end)
    if not recs:
        sys.exit("這個區間沒有物件紀錄")
    me = recs[0]["player_index"]
    ai = next(h["index"] for h in recs[0]["houses"]
              if not h["is_human"] and h["country"] not in ("Neutral", "Special"))

    events = []  # (frame, label)
    if args.execute:
        a = json.loads(Path(args.execute).read_text(encoding="utf-8")).get("attack") or {}
        if a.get("start"):
            events.append((a["start"], "部隊蓋好，出發"))
        if a.get("gathered"):
            events.append((a["gathered"], "集結完成，一起進攻"))
        if a.get("victory"):
            events.append((a["victory"], "AI 被消滅，獲勝"))
        if a.get("defeated"):
            events.append((a["defeated"], "我方被擊敗"))

    def kind_of(o):
        t = db.technos.get(o["type_id"])
        if o["kind"] == "building":
            return "building"
        if t is None or not t.combat_weapons or t.harvester:
            return "other"
        return "unit"

    alive = lambda r: {o["address"]: o for o in r["objects"] if o["on_map"] and o["health"] > 0}
    # Camera: follows our army (and keeps the AI base in view when close), zoomed so the fight
    # fills the frame; eased so it glides instead of jumping.
    base = [(o["x"], o["y"]) for o in recs[0]["objects"] if o["owner"] == ai and o["kind"] == "building"]
    ai_base = (sum(p[0] for p in base) / len(base), sum(p[1] for p in base) / len(base))
    aspect = (0.6 * 12.8) / (0.92 * 7.2)  # width / height of the map axes
    cam = {"c": None, "h": None}

    def camera(objs):
        army = [(o["x"], o["y"]) for o in objs if o["owner"] == me and kind_of(o) == "unit"]
        pts = army or [ai_base]
        med = lambda v: sorted(v)[len(v) // 2]
        cx, cy = med([p[0] for p in pts]), med([p[1] for p in pts])
        # The main body: the 75% of units nearest the median (stragglers do not zoom us out).
        need = sorted(max(abs(p[0] - cx) / aspect, abs(p[1] - cy)) for p in pts)
        reach = need[min(len(need) - 1, int(len(need) * 0.75))]
        if army and math.dist((cx, cy), ai_base) < 30:  # at the base: keep its centre in view
            reach = max(reach, max(abs(ai_base[0] - cx) / aspect, abs(ai_base[1] - cy)))
        half = max(8.0, reach + 4)
        if cam["c"] is None:
            cam["c"], cam["h"] = (cx, cy), half
        k = 0.12
        cam["c"] = (cam["c"][0] + (cx - cam["c"][0]) * k, cam["c"][1] + (cy - cam["c"][1]) * k)
        cam["h"] += (half - cam["h"]) * k
        (x, y), h = cam["c"], cam["h"]
        return x - h * aspect, x + h * aspect, y - h, y + h

    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.02, 0.04, 0.6, 0.92], facecolor=BG)
    side = fig.add_axes([0.64, 0.04, 0.34, 0.92], facecolor=PANEL)
    chart = fig.add_axes([0.67, 0.1, 0.28, 0.25], facecolor=PANEL)

    frames = list(range(recs[0]["frame"], recs[-1]["frame"] + 1, args.step))
    deaths: list[tuple[int, float, float, int]] = []  # frame, x, y, owner
    history: list[tuple[int, int, int]] = []           # frame, ours, theirs
    lost = {me: 0, ai: 0}
    state = {"i": 0, "prev": alive(recs[0])}

    def record_at(f):
        i = state["i"]
        while i + 1 < len(recs) and recs[i + 1]["frame"] <= f:
            i += 1
            now = alive(recs[i])
            for addr, o in state["prev"].items():
                if addr not in now and o["owner"] in lost and kind_of(o) != "other":
                    deaths.append((recs[i]["frame"], o["x"], o["y"], o["owner"]))
                    lost[o["owner"]] += 1
            state["prev"] = now
        state["i"] = i
        return recs[i]

    def draw(f):
        r = record_at(f)
        objs = list(alive(r).values())
        ax.clear(); ax.set_facecolor(BG)
        x0, x1, y0, y1 = camera(objs)
        ax.set_xlim(x0, x1); ax.set_ylim(y1, y0)  # cell y grows downwards on screen
        ax.set_aspect("equal"); ax.axis("off")
        zoom = 30 / (y1 - y0)  # marker sizes follow the zoom
        for o in objs:
            k = kind_of(o)
            own = o["owner"]
            color = OURS if own == me else THEIRS if own == ai else NEUTRAL
            if k == "building":
                size = (110 if own in (me, ai) else 60) * zoom ** 2
                ax.scatter(o["x"], o["y"], s=size, marker="s", c=color, alpha=0.55 if own in (me, ai) else 0.35,
                           edgecolors="none")
            elif k == "unit" and own in (me, ai):
                big = o["kind"] != "infantry"
                ax.scatter(o["x"], o["y"], s=(110 if big else 45) * zoom ** 2, marker="o", c=color,
                           edgecolors="white" if big else "none", linewidths=0.6)
        for df, dx, dy, own in deaths:
            age = f - df
            if 0 <= age < FADE:
                ax.scatter(dx, dy, s=90 * zoom ** 2, marker="x", c=OURS if own == me else THEIRS,
                           alpha=1 - age / FADE, linewidths=1.6)

        ours = sum(1 for o in objs if o["owner"] == me and kind_of(o) == "unit")
        theirs = sum(1 for o in objs if o["owner"] == ai and kind_of(o) == "unit")
        blds = sum(1 for o in objs if o["owner"] == ai and kind_of(o) == "building")
        history.append((f, ours, theirs))

        side.clear(); side.set_facecolor(PANEL); side.axis("off")
        side.set_xlim(0, 1); side.set_ylim(0, 1)
        side.text(0.06, 0.95, args.title, color=TEXT, fontsize=15, weight="bold", va="top")
        secs = f / FPS
        side.text(0.06, 0.86, f"遊戲時間 {int(secs // 60)}:{int(secs % 60):02d}（第 {f} 幀）", color=MUTED, fontsize=11, va="top")
        rows = [("我方部隊", ours, OURS), ("AI 部隊", theirs, THEIRS), ("AI 建築", blds, THEIRS)]
        for j, (label, val, col) in enumerate(rows):
            y = 0.76 - j * 0.075
            side.text(0.06, y, label, color=TEXT, fontsize=13, va="top")
            side.text(0.62, y, str(val), color=col, fontsize=17, weight="bold", va="top")
        side.text(0.06, 0.52, f"陣亡　我方 {lost[me]}　AI {lost[ai]}", color=MUTED, fontsize=12, va="top")
        past = [lab for ef, lab in events if ef <= f]
        if past:
            side.text(0.06, 0.45, "▶ " + past[-1], color="#fca311", fontsize=14, weight="bold", va="top")
        side.text(0.06, 0.39, "部隊數量（紅：我方　藍：AI）　×：陣亡", color=MUTED, fontsize=10, va="top")

        chart.clear(); chart.set_facecolor(PANEL)
        hf = [h[0] / FPS / 60 for h in history]
        chart.plot(hf, [h[1] for h in history], color=OURS, lw=2)
        chart.plot(hf, [h[2] for h in history], color=THEIRS, lw=2)
        chart.set_xlim(frames[0] / FPS / 60, frames[-1] / FPS / 60)
        chart.set_ylim(0, max(30, max(max(h[1], h[2]) for h in history) + 3))
        chart.tick_params(colors=MUTED, labelsize=8)
        for sp in chart.spines.values():
            sp.set_color("#2b3a55")
        chart.set_xlabel("分鐘", color=MUTED, fontsize=9)

    writer = FFMpegWriter(fps=args.fps, bitrate=4000, codec="libx264", extra_args=["-pix_fmt", "yuv420p"])
    with writer.saving(fig, args.out, dpi=100):
        for n, f in enumerate(frames):
            draw(f)
            writer.grab_frame(facecolor=BG)
            if n % 50 == 0:
                print(f"  {n}/{len(frames)}", flush=True)
    print(f"已輸出 {args.out}（{len(frames)} 格，約 {len(frames) / args.fps:.0f} 秒）")


if __name__ == "__main__":
    main()
