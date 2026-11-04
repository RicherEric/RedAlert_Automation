"""Quick cost-effectiveness report: which buildable units deal the most damage per credit
against each armor class. Run extract.py first.
"""
import sys
from pathlib import Path

import pandas as pd

from ra2tools.ini import Ini
from ra2tools.rules import warhead_verses

DATA = Path(__file__).parent / "data"
TARGET_ARMORS = ["none", "flak", "plate", "light", "heavy", "concrete"]


def main(top: int = 8) -> None:
    rules = Ini.from_bytes((DATA / "rulesmd.ini").read_bytes())
    units = pd.read_csv(DATA / "units.csv")
    army = units[units["buildable"] & (units["kind"] != "building") & units["dmg_per_100f"].notna()]
    army = army.drop_duplicates("id")

    for armor in TARGET_ARMORS:
        rows = []
        for _, u in army.iterrows():
            factor = warhead_verses(rules, u["warhead"]).get(armor, 1.0)
            eff = u["dmg_per_100f"] * factor * 1000 / u["cost"]
            rows.append((u["id"], u["name"], u["cost"], round(eff, 1)))
        table = pd.DataFrame(rows, columns=["id", "name", "cost", "dmg/100f per $1000"])
        print(f"\n=== vs armor: {armor} ===")
        print(table.sort_values("dmg/100f per $1000", ascending=False).head(top).to_string(index=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
