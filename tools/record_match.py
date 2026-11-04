"""Record one automated skirmish to video: launch (menus clicked by autostart), build, attack.

    .venv\\Scripts\\python.exe tools\\record_match.py --ai easy --targets E2=20 SHK=2 --out thesis\\videos\\raw

Writes <out>.mp4 (the 800x600 game image, captured from the desktop with ffmpeg gdigrab, so the
game window must stay uncovered), <out>.jsonl (monitor.py object log), <out>_execute.json and
<out>.json with the perf_counter time the capture started, to line the video up with game frames.
Uses the menu launch (--menu): the spawner build pauses whenever its window loses focus.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ra2tools import launcher as L  # noqa: E402

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", default="easy")
    ap.add_argument("--targets", nargs="+", required=True)
    ap.add_argument("--opponent", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    opponent = args.opponent or str(ROOT / "calibration" / "opponents" / f"{args.ai}_americans_firestorm.json")

    start = subprocess.Popen([PY, "autostart.py", "start", "--menu", "--ai", args.ai], cwd=ROOT)
    hwnd = None
    for _ in range(120):
        hwnd = L.game_window()
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        start.kill()
        sys.exit("遊戲視窗沒有出現")
    time.sleep(2)  # let the window settle at its size
    left, top, _ = L.geometry(hwnd)
    rw, rh = L.game_resolution()
    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "gdigrab", "-framerate", "20",
         "-offset_x", str(int(left)), "-offset_y", str(int(top)), "-video_size", f"{rw}x{rh}",
         "-draw_mouse", "0", "-i", "desktop", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out.with_suffix(".mp4"))],
        stdin=subprocess.PIPE)
    t0 = time.perf_counter()
    print(f"錄影開始：視窗 {int(left)},{int(top)} {rw}x{rh}")
    if start.wait() != 0:
        ff.communicate(b"q")
        sys.exit("開局失敗")
    mon = subprocess.Popen([PY, "monitor.py", "watch", "--log-objects", "--quiet", "--duration", str(args.timeout + 60),
                            "--log", str(out.with_suffix(".jsonl"))], cwd=ROOT)
    ex = subprocess.run([PY, "execute.py", "--attack", "--camera", "--targets", *args.targets,
                         "--opponent", opponent, "--timeout", str(args.timeout),
                         "--log", str(out.parent / (out.name + "_execute.json"))], cwd=ROOT)
    time.sleep(3)  # a moment of the end screen
    ff.communicate(b"q", timeout=60)
    mon.terminate()
    (out.with_suffix(".json")).write_text(json.dumps({"capture_start": t0, "window": [left, top, rw, rh],
                                                      "execute_rc": ex.returncode}), encoding="utf-8")
    print("錄影結束")


if __name__ == "__main__":
    main()
