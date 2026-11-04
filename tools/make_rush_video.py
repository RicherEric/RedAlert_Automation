"""Cut a recorded match (tools/record_match.py) into a short sped-up video with captions.

    .venv\\Scripts\\python.exe tools\\make_rush_video.py thesis\\videos\\raw_easy_rush -o thesis\\videos\\rush_easy.mp4

Phases come from execute.py's log (build finished, gathered, won) and are mapped to video time
through the monitor log (perf_counter time of each game frame) and the capture start time. Each
phase is sped up and gets a caption panel on the right; the game image is scaled to 960x720.
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/msjhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msjh.ttc"
W, H, GW = 1280, 720, 960
BG, TEXT, MUTED, ACCENT, RED = (15, 22, 38), (232, 236, 243), (141, 153, 174), (252, 163, 17), (255, 90, 78)


def wrap(draw, text, font, width):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n" or (draw.textlength(cur + ch, font=font) > width and ch not in "，。！、：）」"):
            lines.append(cur)
            cur = "" if ch == "\n" else ch
        else:
            cur += ch
    return lines + [cur] if cur else lines


def panel(path: Path, step: str, title: str, body: str, speed: str, footer: str) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([GW, 0, W, H], fill=BG + (255,))
    x = GW + 28
    d.ellipse([x, 40, x + 46, 86], fill=ACCENT)
    d.text((x + 23, 63), step, font=ImageFont.truetype(FONT, 24), fill=BG, anchor="mm")
    d.text((x + 62, 63), speed, font=ImageFont.truetype(FONT, 20), fill=MUTED, anchor="lm")
    y = 116
    for line in wrap(d, title, ImageFont.truetype(FONT, 30), W - x - 24):
        d.text((x, y), line, font=ImageFont.truetype(FONT, 30), fill=TEXT)
        y += 44
    y += 14
    for line in wrap(d, body, ImageFont.truetype(FONT_REG, 21), W - x - 24):
        d.text((x, y), line, font=ImageFont.truetype(FONT_REG, 21), fill=TEXT)
        y += 34
    for i, line in enumerate(wrap(d, footer, ImageFont.truetype(FONT_REG, 15), W - x - 24)):
        d.text((x, H - 60 + i * 22), line, font=ImageFont.truetype(FONT_REG, 15), fill=MUTED)
    img.save(path)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", help="record_match.py --out prefix")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default="快攻 vs 簡單 AI")
    args = ap.parse_args()
    raw = Path(args.raw)
    cap = json.loads(raw.with_suffix(".json").read_text(encoding="utf-8"))
    ex = json.loads((raw.parent / (raw.name + "_execute.json")).read_text(encoding="utf-8"))
    att = ex.get("attack") or {}

    frames, walls = [], []
    for line in open(raw.with_suffix(".jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if r.get("frame") and (not frames or r["frame"] > frames[-1]):
            frames.append(r["frame"]); walls.append(r["wall_time"])

    def vt(frame: float) -> float:
        """Video seconds at a game frame."""
        i = min(max(bisect.bisect_left(frames, frame), 1), len(frames) - 1)
        f0, f1, w0, w1 = frames[i - 1], frames[i], walls[i - 1], walls[i]
        w = w0 + (w1 - w0) * (frame - f0) / max(f1 - f0, 1)
        return max(0.0, w - cap["capture_start"])

    total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(raw.with_suffix(".mp4"))], capture_output=True, text=True).stdout)
    game0 = vt(frames[0])
    built, gathered = att.get("start"), att.get("gathered")
    won, lost = att.get("victory"), att.get("defeated")
    end = won or lost or frames[-1]
    pred = ex.get("victory_predicted") or {}
    army = att.get("army") or {}
    n_army = sum(army.values())
    offset = ex.get("deploy_frame") or 0
    t_end = min(total, vt(end) + 1.5)

    segs = [  # (start s, end s, speed, step, title, body)
        (min(11.0, game0), game0, 3, "1", "程式自己開遊戲",
         "自動點選單、選出生點、進入遭遇戰，全程不用碰滑鼠。"),
        (game0, vt(built), 8, "2", "照模型決定的順序建造",
         f"訓練好的模型即時決定先蓋什麼：兵營 → 動員兵 → 磁爆步兵。"
         f"模擬預測第 {pred.get('ready', 0):.0f} 幀蓋好，實際第 {built - offset} 幀。"),
        (vt(built), vt(gathered) if gathered else vt(built) + 30, 8, "3", "全軍出發、在敵方基地前集結",
         f"{n_army} 個單位先在敵方基地外集合，等到齊了才一起進攻，不會零星送死。"),
        (vt(gathered) if gathered else vt(built) + 30, t_end, 6, "4", "集中火力，先拆建造場",
         "先打會反擊的單位和碉堡，再拆建造場，讓 AI 沒辦法重建。"),
    ]
    tmp = Path(tempfile.mkdtemp())
    parts = []
    for k, (a, b, speed, step, title, body) in enumerate(segs):
        if b - a < 0.5:
            continue
        png = tmp / f"panel{k}.png"
        panel(png, step, title, body, f"{speed} 倍速", f"{args.title}｜紅色警戒 2 自動對戰")
        part = tmp / f"part{k}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}",
                        "-i", str(raw.with_suffix(".mp4")), "-i", str(png), "-filter_complex",
                        f"[0:v]setpts=(PTS-STARTPTS)/{speed},fps=30,scale={GW}:{H},pad={W}:{H}:0:0[g];[g][1:v]overlay=0:0",
                        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(part)], check=True)
        parts.append(part)
        print(f"  段 {step}：{a:.0f}–{b:.0f} 秒，{speed} 倍 → {(b - a) / speed:.1f} 秒")
    # End card: the last frame held for 3 s with the result.
    left = sum(n for n in (att.get("survivors") or {}).values())
    if won:
        v = pred.get("victory") or 1
        title, body = "勝利！", (f"第 {won - offset} 幀（約 {(won - offset) / 20 / 60:.1f} 分鐘）擊敗 AI，"
                                f"{n_army} 個單位剩 {left} 個。模擬預測第 {v:.0f} 幀，"
                                f"誤差 {((won - offset) - v) / v:+.1%}。")
    else:
        title, body = "這場沒有打贏", "部隊全滅或時間到，下一步看戰後分析找原因。"
    png = tmp / "panel_end.png"
    panel(png, "勝" if won else "!", title, body, "", f"{args.title}｜紅色警戒 2 自動對戰")
    still = tmp / "last.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t_end - 0.3):.2f}",
                    "-i", str(raw.with_suffix(".mp4")), "-frames:v", "1", str(still)], check=True)
    part = tmp / "part_end.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-t", "3", "-i", str(still), "-i", str(png),
                    "-filter_complex", f"[0:v]fps=30,scale={GW}:{H},pad={W}:{H}:0:0[g];[g][1:v]overlay=0:0",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(part)], check=True)
    parts.append(part)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", args.out], check=True)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", args.out],
                         capture_output=True, text=True).stdout.strip()
    print(f"已輸出 {args.out}（{float(dur):.0f} 秒）")


if __name__ == "__main__":
    main()
