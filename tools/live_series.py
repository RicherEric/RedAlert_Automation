"""Back-to-back live games vs the hard AI with one fixed plan, results appended to a TSV.
Menu launches move the real mouse, so each game waits until the user has been idle for
--idle seconds (GetLastInputInfo). Skirmish vs the computer only; RA2MD.INI is restored after
every game by autostart.py stop.

    .venv\\Scripts\\python.exe tools\\live_series.py --until 23:30
    .venv\\Scripts\\python.exe tools\\live_series.py --name series_v4 --targets APOC=9 ZEP=6 --early HTK=8 SENGINEER=2
"""
from __future__ import annotations

import argparse
import ctypes
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
TARGETS = ["APOC=9", "FLAKT=1", "ZEP=6"]
EARLY = ["HTK=8", "FLAKT=1", "HARV=5"]
COLUMNS = ["場次", "開始", "結果", "獲勝幀", "被擊敗幀", "出發幀", "花費", "出發部隊", "備註",
           "方案", "油井", "進駐", "維修", "追加", "補兵"]


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def derricks_held(log: Path) -> str:
    """Most oil derricks we held at once, from the monitor log (sampled)."""
    if not log.exists():
        return ""
    best = 0
    with open(log, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i % 50:
                continue
            s = json.loads(line)
            me = s.get("player_index")
            best = max(best, sum(1 for o in s["objects"] if o["type_id"] == "CAOILD" and o["owner"] == me))
    return str(best)


def result(path: Path, text: Path | None = None) -> dict:
    """From execute.py's log; a base lost before the attack only shows in its printed output."""
    if not path.exists():
        return {"result": "沒有紀錄"}
    d = json.loads(path.read_text(encoding="utf-8"))
    a = d.get("attack") or {}
    lost = re.search(r"(\d+) 幀  我方被擊敗", text.read_text(encoding="utf-8")) if text and text.exists() else None
    defeated = a.get("defeated") or (int(lost.group(1)) if lost else None)
    res = "贏" if a.get("victory") else "輸" if defeated else "逾時"
    steward = d.get("steward") or []
    count = lambda what: sum(1 for e in steward if e[1] == what)
    return {"result": res, "victory": a.get("victory"), "defeated": defeated, "depart": a.get("start"),
            "army": a.get("army"), "spent": d.get("spent"),
            "derricks": derricks_held(text.with_suffix(".jsonl")) if text else "",
            "garrison": count("garrison"), "repairs": count("repair"),
            "adapted": "; ".join(f"{f}:{add}" for f, _, add in d.get("adapted") or []),
            "reinforced": len(d.get("reinforced") or [])}


def play(tag: str, timeout: int, targets: list[str], early: list[str]) -> dict:
    if subprocess.run([PY, "autostart.py", "start", "--menu", "--ai", "hard"], cwd=ROOT).returncode != 0:
        subprocess.run([PY, "autostart.py", "stop"], cwd=ROOT)
        return {"result": "開局失敗"}
    mon = subprocess.Popen([PY, "monitor.py", "watch", "--log-objects", "--quiet", "--duration", str(timeout + 100),
                            "--log", f"logs/{tag}.jsonl"], cwd=ROOT)
    try:
        with open(ROOT / "logs" / f"{tag}.txt", "w", encoding="utf-8") as out:
            subprocess.run([PY, "execute.py", "--attack", "--targets", *targets, *(["--early", *early] if early else []),
                            "--opponent", "calibration/opponents/hard_q80.json",
                            "--scenario", "scenarios/soviet_vs_hard_quantile.toml", "--timeout", str(timeout),
                            "--log", f"logs/{tag}_execute.json"], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    finally:
        kill_tree(mon.pid)
        time.sleep(3)
        subprocess.run([PY, "autostart.py", "stop"], cwd=ROOT)
    return result(ROOT / "logs" / f"{tag}_execute.json", ROOT / "logs" / f"{tag}.txt")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="23:30", help="不再開新局的時間（今天）")
    ap.add_argument("--max-games", type=int, default=30)
    ap.add_argument("--idle", type=float, default=120, help="使用者閒置幾秒才開局")
    ap.add_argument("--timeout", type=int, default=2800)
    ap.add_argument("--first", type=int, default=1, help="第一場的編號")
    ap.add_argument("--targets", nargs="+", default=TARGETS)
    ap.add_argument("--early", nargs="*", default=EARLY)
    ap.add_argument("--name", default="series_hard", help="表格與記錄的檔名開頭")
    args = ap.parse_args()
    day = datetime.now().strftime("%Y%m%d")
    stop_at = datetime.combine(datetime.now().date(), datetime.strptime(args.until, "%H:%M").time())
    table = ROOT / "logs" / f"{args.name}_{day}.tsv"
    if not table.exists():
        table.write_text("\t".join(COLUMNS) + "\n", encoding="utf-8")
    plan = " ".join(args.targets) + (" | 先蓋 " + " ".join(args.early) if args.early else "")
    fails = 0
    for n in range(args.first, args.first + args.max_games):
        while datetime.now() < stop_at and idle_seconds() < args.idle:
            time.sleep(10)
        if datetime.now() >= stop_at:
            break
        tag = f"{args.name}_{day}_{n}"
        start = datetime.now().strftime("%H:%M")
        print(f"[{start}] 第 {n} 場開局")
        r = play(tag, args.timeout, args.targets, args.early)
        fails = fails + 1 if r["result"] == "開局失敗" else 0
        with open(table, "a", encoding="utf-8") as f:
            f.write("\t".join(str(x if x is not None else "") for x in (
                n, start, r["result"], r.get("victory"), r.get("defeated"), r.get("depart"), r.get("spent"),
                json.dumps(r.get("army") or {}, ensure_ascii=False), "", plan, r.get("derricks"),
                r.get("garrison"), r.get("repairs"), r.get("adapted"), r.get("reinforced"))) + "\n")
        print(f"[{datetime.now():%H:%M}] 第 {n} 場：{r['result']}")
        if fails >= 2:
            print("連續兩次開局失敗，停止")
            break
    print("連打結束")


if __name__ == "__main__":
    main()
