"""Control a running Yuri's Revenge game through ra2bridge.dll (skirmish / LAN only).

    .venv\\Scripts\\python.exe control.py ping                 # inject if needed, check the link
    .venv\\Scripts\\python.exe control.py produce NAPOWR
    .venv\\Scripts\\python.exe control.py place NAPOWR [x y]
    .venv\\Scripts\\python.exe control.py build NAPOWR         # produce, wait until done, place
    .venv\\Scripts\\python.exe control.py deploy SMCV          # by type ID (first one) or address 0x...
    .venv\\Scripts\\python.exe control.py move HTNK 40 50
"""
from __future__ import annotations

import argparse
import sys
import time

from ra2tools import launcher
from ra2tools.bridge import Bridge, BridgeError
from ra2tools.memory import GameMemory, GameNotFound, attach


def connect() -> Bridge:
    pids = launcher.process_ids()
    if not pids:
        sys.exit("遊戲沒有在執行（可以先用 autostart.py start）")
    return Bridge.connect(pids[0])


def build(bridge: Bridge, type_id: str, timeout: float = 120) -> None:
    gm = GameMemory(attach())
    print(bridge.produce(type_id))
    start = gm.frame()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            cell = bridge.place(type_id)
            print(f"OK 在第 {gm.frame()} 幀擺放於 {cell}（建造 {gm.frame() - start} 幀）")
            return
        except BridgeError as e:
            if "not finished" not in str(e) and "not in production" not in str(e):
                raise
        time.sleep(0.25)
    raise BridgeError(f"{type_id} 等了 {timeout:.0f} 秒還沒蓋好")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Control RA2:YR via ra2bridge.dll")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    for name in ("produce", "abandon", "build"):
        sub.add_parser(name).add_argument("type_id")
    p = sub.add_parser("place")
    p.add_argument("type_id")
    p.add_argument("cell", nargs="*", type=int)
    sub.add_parser("deploy").add_argument("unit", help="單位種類 ID（例如 SMCV）或記憶體位址 0x...")
    p = sub.add_parser("move")
    p.add_argument("unit")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    args = ap.parse_args()
    try:
        with connect() as bridge:
            if args.cmd == "ping":
                print(bridge.ping())
            elif args.cmd == "produce":
                print(bridge.produce(args.type_id.upper()))
            elif args.cmd == "abandon":
                print(bridge.abandon(args.type_id.upper()))
            elif args.cmd == "place":
                print(bridge.place(args.type_id.upper(), tuple(args.cell) if len(args.cell) == 2 else None))
            elif args.cmd == "deploy":
                print(bridge.deploy(args.unit))
            elif args.cmd == "move":
                print(bridge.move(args.unit, (args.x, args.y)))
            elif args.cmd == "build":
                build(bridge, args.type_id.upper())
    except (BridgeError, GameNotFound) as e:
        sys.exit(f"失敗：{e}")


if __name__ == "__main__":
    main()
