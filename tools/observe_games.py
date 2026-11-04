"""Observation games for the quantile opponent: we build an army and only defend, the AI plays on.

    .venv\\Scripts\\python.exe tools\\observe_games.py --ai hard --games 8 --targets HTNK=10

Each game: autostart (menu launch), monitor.py object log to logs/observe_<ai>_<n>.jsonl,
execute.py --defend until --minutes, then autostart stop (closes the game, restores RA2MD.INI).
A failed game is logged and the next one starts. Afterwards:
    opponent.py quantile logs\\ai_hard.jsonl logs\\observe_hard_*.jsonl ... --template ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", default="hard")
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--first", type=int, default=1, help="第一場的編號（接續之前的紀錄）")
    ap.add_argument("--targets", nargs="+", default=["HTNK=10"])
    ap.add_argument("--minutes", type=float, default=18, help="每場進遊戲後觀察幾分鐘")
    args = ap.parse_args()
    opponent = ROOT / "calibration" / "opponents" / f"{args.ai}_americans_firestorm.json"
    summary = []
    for n in range(args.first, args.first + args.games):
        tag = f"observe_{args.ai}_{n}"
        log = ROOT / "logs" / f"{tag}.jsonl"
        print(f"\n===== 第 {n} 場（{args.ai}）{time.strftime('%H:%M:%S')} =====")
        row = {"game": n, "log": str(log), "start": time.strftime("%H:%M:%S")}
        try:
            if subprocess.run([PY, "autostart.py", "start", "--menu", "--ai", args.ai], cwd=ROOT).returncode != 0:
                raise RuntimeError("開局失敗")
            secs = args.minutes * 60
            mon = subprocess.Popen([PY, "monitor.py", "watch", "--log-objects", "--quiet", "--duration", str(secs + 30),
                                    "--log", str(log)], cwd=ROOT)
            ex = subprocess.run([PY, "execute.py", "--defend", "--targets", *args.targets, "--opponent", str(opponent),
                                 "--timeout", str(secs), "--log", str(ROOT / "logs" / f"{tag}_execute.json")], cwd=ROOT)
            mon.terminate()
            row["execute_rc"] = ex.returncode
        except Exception as e:  # noqa: BLE001 - keep going with the next game
            row["error"] = str(e)
            print(f"第 {n} 場失敗：{e}")
        finally:
            subprocess.run([PY, "autostart.py", "stop"], cwd=ROOT)
            time.sleep(5)
        last = None
        if log.exists():
            for line in open(log, encoding="utf-8"):
                last = json.loads(line).get("frame", last)
        row["last_frame"] = last
        summary.append(row)
        print(f"第 {n} 場結束：紀錄到第 {last} 幀")
        (ROOT / "logs" / f"observe_{args.ai}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n全部結束")


if __name__ == "__main__":
    main()
