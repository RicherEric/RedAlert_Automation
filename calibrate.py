"""Calibrate simulator assumptions against the real game.

    .venv\\Scripts\\python.exe calibrate.py tests                          # what to measure and how
    .venv\\Scripts\\python.exe calibrate.py record build --item NAPOWR --speed 6
    .venv\\Scripts\\python.exe calibrate.py record move --unit HTNK --cells 10
    .venv\\Scripts\\python.exe calibrate.py record harvest --unit HARV
    .venv\\Scripts\\python.exe calibrate.py add build --item NAPOWR --seconds 6.3,6.4   # stopwatch values
    .venv\\Scripts\\python.exe calibrate.py list
    .venv\\Scripts\\python.exe calibrate.py drop 3
    .venv\\Scripts\\python.exe calibrate.py fit                            # -> calibration/calibration.toml
    .venv\\Scripts\\python.exe calibrate.py auto logs\\game1.jsonl          # from a monitor.py watch log

While recording, hotkeys work with the game in the foreground:
    F9 = mark    F8 = undo last mark    F10 = finish
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from ra2tools.calibration import (TESTS, Measurement, MeasurementStore, fit, fit_log, fit_objects, marks_to_intervals,
                                 write_toml)
from ra2tools.gamedata import GameData

ROOT = Path(__file__).parent
STORE = MeasurementStore(ROOT / "calibration" / "measurements.json")
OUTPUT = ROOT / "calibration" / "calibration.toml"


def beep(freq: int) -> None:
    try:
        import winsound
        threading.Thread(target=winsound.Beep, args=(freq, 90), daemon=True).start()
    except ImportError:
        print("\a", end="", flush=True)


def record_hotkeys() -> list[float]:
    from pynput import keyboard

    marks: list[float] = []
    done = threading.Event()

    def on_press(key):
        if key == keyboard.Key.f9:
            marks.append(time.perf_counter())
            beep(1200)
            print(f"  標記 #{len(marks)}" + (f"  (+{marks[-1] - marks[-2]:.2f}s)" if len(marks) > 1 else ""),
                  flush=True)
        elif key == keyboard.Key.f8 and marks:
            marks.pop()
            beep(500)
            print(f"  取消，剩 {len(marks)} 個標記", flush=True)
        elif key == keyboard.Key.f10:
            beep(800)
            done.set()
            return False

    with keyboard.Listener(on_press=on_press):
        done.wait()
    return marks


def record_console() -> list[float]:
    marks: list[float] = []
    print("  Enter = 標記，u + Enter = 取消上一個，q + Enter = 結束")
    while True:
        cmd = input().strip().lower()
        if cmd == "q":
            return marks
        if cmd == "u" and marks:
            marks.pop()
            print(f"  取消，剩 {len(marks)} 個標記")
        elif cmd == "":
            marks.append(time.perf_counter())
            print(f"  標記 #{len(marks)}" + (f"  (+{marks[-1] - marks[-2]:.2f}s)" if len(marks) > 1 else ""))


def params_from(args, test: str, db: GameData) -> dict:
    params = {}
    for need in TESTS[test]["needs"]:
        value = getattr(args, need, None)
        if value in (None, ""):
            sys.exit(f"{test} 需要 --{need}")
        if need in ("item", "unit"):
            value = value.upper()
            if value not in db.technos:
                sys.exit(f"找不到單位/建築 ID：{value}（用 ra2opt.py units <國家> 查）")
        params[need] = value
    return params


def describe(db: GameData, test: str, params: dict) -> None:
    spec = TESTS[test]
    print(f"\n【{spec['title']}】")
    for line in spec["howto"]:
        print(f"  - {line}")
    for key in ("item", "unit"):
        if key in params:
            t = db.technos[params[key]]
            extra = f"，規則建造時間 {db.build_frames(t):.0f} 幀" if key == "item" else f"，Speed={t.speed}"
            print(f"  對象：{t.name}({t.id}){extra}")


def cmd_tests(db: GameData, args) -> None:
    for name, spec in TESTS.items():
        needs = " ".join(f"--{n} ..." for n in spec["needs"])
        print(f"\n{name} {needs}\n  {spec['title']}")
        for line in spec["howto"]:
            print(f"    - {line}")
    print("\n建議順序：build（必要，其他項目都靠它換算幀數）→ move → harvest → 其他")


def cmd_record(db: GameData, args) -> None:
    params = params_from(args, args.test, db)
    describe(db, args.test, params)
    if args.console:
        marks = record_console()
    else:
        print("\n  切到遊戲：F9 = 標記，F8 = 取消上一個，F10 = 結束錄製")
        marks = record_hotkeys()
    intervals = marks_to_intervals(args.test, marks)
    if not intervals:
        print("沒有完整的量測，未儲存")
        return
    idx = STORE.add(Measurement(args.test, intervals, params, args.speed))
    print(f"\n已儲存 #{idx}: {args.test} {params} 間隔(秒) = {', '.join(f'{x:.2f}' for x in intervals)}")


def cmd_add(db: GameData, args) -> None:
    params = params_from(args, args.test, db)
    try:
        intervals = [float(x) for x in args.seconds.split(",") if x.strip()]
    except ValueError:
        sys.exit("--seconds 格式：以逗號分隔的秒數，例如 6.3,6.4")
    idx = STORE.add(Measurement(args.test, intervals, params, args.speed))
    print(f"已儲存 #{idx}: {args.test} {params} {intervals}")


def cmd_list(db: GameData, args) -> None:
    items = STORE.load()
    if not items:
        print("還沒有量測資料")
    for i, m in enumerate(items):
        speed = f" 速度={m.game_speed}" if m.game_speed else ""
        print(f"#{i:<3} {m.recorded}  {m.test:<8} {m.params}{speed}  "
              f"[{', '.join(f'{x:.2f}' for x in m.intervals)}]")


def cmd_drop(db: GameData, args) -> None:
    items = STORE.load()
    if not 0 <= args.index < len(items):
        sys.exit("編號不存在")
    removed = items.pop(args.index)
    STORE.save(items)
    print(f"已刪除 #{args.index}: {removed.test} {removed.params}")


def cmd_fit(db: GameData, args) -> None:
    result = fit(db, STORE.load())
    for note in result.notes:
        print(f"  * {note}")
    if not result.values:
        return
    print("\n參數                         擬合值      樣本數  離散(最小~最大)")
    for key, value in result.values.items():
        xs = result.samples.get(key, [])
        print(f"  {key:<26} {value:>10.4g}  {len(xs):>5}   {min(xs):.4g} ~ {max(xs):.4g}")
    write_toml(OUTPUT, result)
    print(f"\n已寫入 {OUTPUT}（ra2opt.py 會自動套用）")


def cmd_auto(db: GameData, args) -> None:
    records = [json.loads(line) for line in Path(args.log).read_text(encoding="utf-8").splitlines() if line.strip()]
    result = fit_log(db, records)
    if any(r.get("objects") for r in records):
        extra = fit_objects(db, records)
        result.values.update(extra.values)
        result.samples.update(extra.samples)
        result.notes += extra.notes
    if args.bot:
        # Orders came from execute.py: delays are the bot's, not the player's reaction time. The
        # deploy order was not given at frame 0, so mcv_deploy_frames is meaningless here
        # (execute.py prints bot_mcv_deploy_frames itself).
        result.values.pop("mcv_deploy_frames", None)
        if "place_delay_frames" in result.values:
            result.values["bot_place_delay_frames"] = result.values.pop("place_delay_frames")
            result.samples["bot_place_delay_frames"] = result.samples.pop("place_delay_frames", [])
    for note in result.notes:
        print(f"  * {note}")
    if result.values:
        print("\n參數                         擬合值      樣本數")
        for key, value in result.values.items():
            print(f"  {key:<26} {value:>10.4g}  {len(result.samples.get(key, [])):>5}")
        for speed, fps in sorted(result.fps_by_speed.items()):
            print(f"  fps @ 速度設定 {speed:<14} {fps:>10.4g}")
        write_toml(OUTPUT, result, merge=True)
        print(f"\n已合併寫入 {OUTPUT}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="RA2:YR simulator calibration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tests")
    for name in ("record", "add"):
        p = sub.add_parser(name)
        p.add_argument("test", choices=list(TESTS))
        p.add_argument("--item")
        p.add_argument("--unit")
        p.add_argument("--cells", type=float)
        p.add_argument("--speed", help="遊戲速度設定（記錄用，例如 6 或 fastest）")
        if name == "record":
            p.add_argument("--console", action="store_true", help="不用全域熱鍵，改在終端機按 Enter")
        else:
            p.add_argument("--seconds", required=True, help="逗號分隔的秒數")
    sub.add_parser("list")
    sub.add_parser("drop").add_argument("index", type=int)
    sub.add_parser("fit")
    p = sub.add_parser("auto")
    p.add_argument("log", help="monitor.py watch --log 產生的 JSONL")
    p.add_argument("--bot", action="store_true", help="這局是 execute.py 下的指令（延遲存成 bot_*）")
    args = ap.parse_args()
    db = GameData.load(ROOT / "data")
    {"tests": cmd_tests, "record": cmd_record, "add": cmd_add, "list": cmd_list,
     "drop": cmd_drop, "fit": cmd_fit, "auto": cmd_auto}[args.cmd](db, args)


if __name__ == "__main__":
    main()
