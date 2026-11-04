"""Extract YR data files from the game's MIX archives and build a unit database.

Usage:
    .venv\\Scripts\\python.exe extract.py ["<game dir>"]
"""
import sys
from pathlib import Path

from ra2tools.csf import parse_csf
from ra2tools.ini import Ini
from ra2tools.mix import MixLibrary
from ra2tools.rules import load_technos

DEFAULT_GAME_DIR = r"D:\SteamLibrary\steamapps\common\Command & Conquer Red Alert II"
FILES = ["rulesmd.ini", "artmd.ini", "aimd.ini", "soundmd.ini", "ra2md.csf"]
OUT = Path(__file__).parent / "data"


def main() -> None:
    game_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GAME_DIR
    OUT.mkdir(exist_ok=True)
    lib = MixLibrary(game_dir)

    for name in FILES:
        found = lib.find(name)
        if found is None:
            print(f"[missing] {name}")
            continue
        source, raw = found
        (OUT / name).write_bytes(raw)
        print(f"[ok] {name:<12} {len(raw):>8} bytes  <- {source}")

    rules = Ini.from_bytes((OUT / "rulesmd.ini").read_bytes())
    strings = parse_csf((OUT / "ra2md.csf").read_bytes())
    units = load_technos(rules, strings)
    units.to_csv(OUT / "units.csv", index=False, encoding="utf-8-sig")
    print(f"[ok] units.csv    {len(units)} technos, {int(units['buildable'].sum())} buildable")


if __name__ == "__main__":
    main()
