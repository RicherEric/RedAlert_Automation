"""Live read-only view of a running Yuri's Revenge game.

    .venv\\Scripts\\python.exe monitor.py check                  # verify addresses against the running exe
    .venv\\Scripts\\python.exe monitor.py watch                  # live status every second
    .venv\\Scripts\\python.exe monitor.py watch --log logs\\game1.jsonl --interval 0.5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from ra2tools.gamedata import GameData
from ra2tools.memory import GameMemory, GameNotFound, LiveFps, Snapshot, attach

ROOT = Path(__file__).parent


def connect() -> GameMemory:
    try:
        return GameMemory(attach())
    except GameNotFound as e:
        sys.exit(str(e))


def cmd_check(args) -> None:
    gm = connect()
    print("已連上 gamemd.exe，檢查中（約 1 秒）...")
    problems = gm.sanity_check()
    if problems:
        print("發現問題：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    snap = gm.snapshot()
    print(f"位址檢查通過：第 {snap.frame} 幀，速度設定 {snap.game_speed}，遊戲回報 {snap.frame_rate} fps")
    for h in snap.houses:
        tag = "（你）" if h.index == snap.player_index else ""
        print(f"  陣營 {h.index}: {h.country:<14} ${h.credits:<7} 電力 {h.power_output}/{h.power_drain}"
              f" {'人類' if h.is_human else '電腦'}{tag}")
    print(f"  場上物件 {len(snap.objects)} 個")


def names(db: GameData | None, tid: str | None) -> str:
    if not tid:
        return "-"
    t = db.technos.get(tid) if db else None
    return f"{t.name}({tid})" if t else tid


def render(snap: Snapshot, live_fps: float | None, db: GameData | None) -> str:
    lines = []
    fps = f"  實測 {live_fps:.1f} fps" if live_fps else "  （暫停中或剛開始）"
    lines.append(f"第 {snap.frame} 幀  速度設定 {snap.game_speed}  遊戲回報 {snap.frame_rate} fps{fps}")
    p = snap.player
    if p:
        lines.append(f"你：{p.country}  ${p.credits}  電力 {p.power_output}/{p.power_drain}")
        for f in snap.factories:
            if f.type_id:
                hold = " 暫停" if f.on_hold else ""
                lines.append(f"  生產[{f.category}] {names(db, f.type_id)} {f.progress * 100:5.1f}%{hold}")
        counts = snap.counts(p.index)
        if counts:
            lines.append("  擁有：" + ", ".join(f"{names(db, t)}x{n}" for t, n in sorted(counts.items())))
    for h in snap.houses:
        if h.index != snap.player_index and h.country not in ("Special", "Neutral"):
            n = sum(snap.counts(h.index).values())
            lines.append(f"敵/友 {h.index}: {h.country} ${h.credits} 物件 {n}{' 已戰敗' if h.defeated else ''}")
    return "\n".join(lines)


def cmd_watch(args) -> None:
    gm = connect()
    db = GameData.load(ROOT / "data") if (ROOT / "data" / "rulesmd.ini").exists() else None
    log = None
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        log = open(args.log, "a", encoding="utf-8")
        print(f"記錄到 {args.log}")
    live = LiveFps()
    end = time.monotonic() + args.duration if args.duration else None
    try:
        while end is None or time.monotonic() < end:
            if not gm.in_game():
                print("\r等待進入遊戲...", end="", flush=True)
                time.sleep(1)
                continue
            snap = gm.snapshot()
            if not args.quiet:
                print("\033[2J\033[H" + render(snap, live.update(snap.wall_time, snap.frame, snap.game_speed), db), flush=True)
            if log:
                record = asdict(snap)
                if not args.log_objects:
                    record.pop("objects")
                    record["counts"] = {str(h.index): snap.counts(h.index) for h in snap.houses}
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n結束")
    finally:
        if log:
            log.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="RA2:YR live memory monitor (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=1.0)
    w.add_argument("--log", help="寫入 JSONL 日誌，供自動校準使用")
    w.add_argument("--log-objects", action="store_true", help="日誌包含每個物件的位置（檔案較大）")
    w.add_argument("--duration", type=float, help="幾秒後自動結束")
    w.add_argument("--quiet", action="store_true", help="不刷新畫面（背景記錄用）")
    args = ap.parse_args()
    {"check": cmd_check, "watch": cmd_watch}[args.cmd](args)


if __name__ == "__main__":
    main()
