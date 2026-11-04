"""Build unit / weapon tables from rulesmd.ini."""
from __future__ import annotations

import pandas as pd

from .ini import Ini

# YR armor types, in the order Verses= lists them.
ARMOR_TYPES = ["none", "flak", "plate", "light", "medium", "heavy", "wood", "steel",
               "concrete", "special_1", "special_2"]

TECHNO_REGISTRIES = {
    "InfantryTypes": "infantry",
    "VehicleTypes": "vehicle",
    "AircraftTypes": "aircraft",
    "BuildingTypes": "building",
}


def warhead_verses(rules: Ini, warhead: str) -> dict[str, float]:
    values = rules.get_list(warhead, "Verses")
    verses = {}
    for i, armor in enumerate(ARMOR_TYPES):
        if i < len(values):
            v = values[i]
            verses[armor] = float(v.rstrip("%")) / 100 if v.endswith("%") else float(v)
        else:
            verses[armor] = 1.0
    return verses


def weapon_stats(rules: Ini, weapon: str | None) -> dict:
    if not weapon or weapon.lower() == "none" or weapon not in rules:
        return {}
    rof = rules.get_int(weapon, "ROF", 1) or 1
    burst = rules.get_int(weapon, "Burst", 1) or 1
    damage = rules.get_int(weapon, "Damage")
    return {
        "weapon": weapon,
        "damage": damage,
        "rof": rof,
        "burst": burst,
        "range": rules.get_float(weapon, "Range"),
        "warhead": rules.get(weapon, "Warhead"),
        "projectile": rules.get(weapon, "Projectile"),
        # Raw damage per 100 game frames before armor modifiers.
        "dmg_per_100f": damage * burst * 100 / rof,
    }


def load_technos(rules: Ini, strings: dict[str, str] | None = None) -> pd.DataFrame:
    strings = strings or {}
    rows = []
    for registry, kind in TECHNO_REGISTRIES.items():
        for unit in rules.registry(registry):
            sec = rules.section(unit)
            if not sec:
                continue
            ui_label = sec.get("UIName", "")
            primary = weapon_stats(rules, sec.get("Primary"))
            rows.append({
                "id": unit,
                "kind": kind,
                "name": strings.get(ui_label.upper(), sec.get("Name", unit)),
                "owner": sec.get("Owner", ""),
                "techlevel": rules.get_int(unit, "TechLevel", -1),
                "cost": rules.get_int(unit, "Cost"),
                "strength": rules.get_int(unit, "Strength"),
                "armor": sec.get("Armor", "none").lower(),
                "speed": rules.get_int(unit, "Speed"),
                "sight": rules.get_int(unit, "Sight"),
                "prerequisite": sec.get("Prerequisite", ""),
                "build_time_mult": rules.get_float(unit, "BuildTimeMultiplier", 1.0),
                "power": rules.get_int(unit, "Power"),
                "primary": primary.get("weapon"),
                "secondary": sec.get("Secondary"),
                "damage": primary.get("damage"),
                "rof": primary.get("rof"),
                "burst": primary.get("burst"),
                "range": primary.get("range"),
                "warhead": primary.get("warhead"),
                "dmg_per_100f": primary.get("dmg_per_100f"),
            })
    df = pd.DataFrame(rows)
    df["buildable"] = (df["techlevel"].between(0, 10)) & (df["owner"] != "") & (df["cost"] > 0)
    return df


def effectiveness_matrix(rules: Ini, units: pd.DataFrame) -> pd.DataFrame:
    """Armor-adjusted damage per 100 frames per 1000 credits: attacker rows x defender columns.

    Ignores range, targeting restrictions (e.g. anti-air only), and veterancy.
    """
    attackers = units[units["dmg_per_100f"].notna() & (units["cost"] > 0)]
    defenders = units[units["strength"] > 0]
    data = {}
    for _, a in attackers.iterrows():
        verses = warhead_verses(rules, a["warhead"]) if a["warhead"] else {}
        data[a["id"]] = {
            d["id"]: a["dmg_per_100f"] * verses.get(d["armor"], 1.0) * 1000 / a["cost"]
            for _, d in defenders.iterrows()
        }
    return pd.DataFrame(data).T
