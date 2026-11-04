"""Typed game database built from rulesmd.ini + ra2md.csf, shared by the simulators."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .csf import parse_csf
from .ini import Ini
from .rules import ARMOR_TYPES, TECHNO_REGISTRIES, warhead_verses

# The production bar has 54 steps and each step lasts a whole number of frames (truncated),
# verified in game: e.g. 1260-frame rules time -> 23 frames/step -> 1242 frames.
PRODUCTION_STEPS = 54


def _f32(x: float) -> float:
    """Round to the nearest 32-bit float, as the engine stores rules values."""
    return struct.unpack("<f", struct.pack("<f", x))[0]

COUNTRIES = {
    "Americans": ("allied", "美國"),
    "Alliance": ("allied", "韓國"),
    "French": ("allied", "法國"),
    "Germans": ("allied", "德國"),
    "British": ("allied", "英國"),
    "Russians": ("soviet", "蘇俄"),
    "Confederation": ("soviet", "古巴"),
    "Africans": ("soviet", "利比亞"),
    "Arabs": ("soviet", "伊拉克"),
    "YuriCountry": ("yuri", "尤里"),
}

SIDE_BASICS = {
    "allied": {"cnst": "GACNST", "power": "GAPOWR", "proc": "GAREFN", "barracks": "GAPILE",
               "factory": "GAWEAP", "radar": "GAAIRC", "tech": "GATECH", "harvester": "CMIN"},
    "soviet": {"cnst": "NACNST", "power": "NAPOWR", "proc": "NAREFN", "barracks": "NAHAND",
               "factory": "NAWEAP", "radar": "NARADR", "tech": "NATECH", "harvester": "HARV"},
    "yuri": {"cnst": "YACNST", "power": "YAPOWR", "proc": "YAREFN", "barracks": "YABRCK",
             "factory": "YAWEAP", "radar": "NAPSIS", "tech": "YATECH", "harvester": "SMIN"},
}

# Generic prerequisite tokens -> [General] key listing the buildings that satisfy them.
GENERIC_PREREQS = {
    "POWER": "PrerequisitePower",
    "FACTORY": "PrerequisiteFactory",
    "BARRACKS": "PrerequisiteBarracks",
    "RADAR": "PrerequisiteRadar",
    "TECH": "PrerequisiteTech",
    "PROC": "PrerequisiteProc",
}

# Mechanics the combat simulator does not model; weapons using them are skipped.
UNSUPPORTED_WARHEAD_FLAGS = ["MindControl", "IvanBomb", "Temporal", "Parasite", "IsLocomotor",
                             "Psychedelic"]
# SabotageCursor = C4 charges (buildings / ships only).
UNSUPPORTED_WEAPON_FLAGS = ["Spawner", "DrainWeapon", "LimboLaunch", "SabotageCursor"]

FACTORY_CATEGORY = {"BuildingType": "building", "InfantryType": "infantry",
                    "UnitType": "vehicle", "AircraftType": "aircraft"}


def _yes(value: str | None) -> bool:
    return bool(value) and value.strip().lower()[:1] in ("y", "t", "1")


@dataclass
class Weapon:
    id: str
    damage: float
    rof: int
    range: float
    min_range: float
    burst: int
    verses: np.ndarray  # multiplier per armor index
    aa: bool
    ag: bool
    cell_spread: float
    percent_at_max: float
    suicide: bool
    unsupported: str | None  # reason the simulator can't model it, if any

    @property
    def usable(self) -> bool:
        return self.unsupported is None and self.damage > 1


@dataclass
class Techno:
    id: str
    name: str
    kind: str  # infantry / vehicle / aircraft / building
    cost: int
    strength: int
    armor: int  # index into ARMOR_TYPES
    speed: int
    owners: set[str]
    required_houses: set[str]
    forbidden_houses: set[str]
    prerequisites: list[str]
    prerequisite_override: list[str]
    techlevel: int
    build_time_mult: float
    power: int
    factory: str | None  # for buildings: which queue category they enable
    weapons: list[Weapon] = field(default_factory=list)
    ammo: int = -1
    naval: bool = False
    free_unit: str | None = None
    storage: int = 0
    teleporter: bool = False
    harvester: bool = False
    enslaves: str | None = None
    slaves_number: int = 0
    build_limit: int = 0
    crusher: bool = False
    requires_stolen_tech: bool = False  # needs a spy to infiltrate an enemy barracks
    flies: bool = False  # MovementZone=Fly vehicles (Kirov, Siege Chopper) count as aircraft
    deploy_fire: bool = False  # Secondary only usable while deployed (GI, Guardian GI, Siege Chopper)

    @property
    def is_air(self) -> bool:
        return self.kind == "aircraft" or self.flies

    @property
    def category(self) -> str:
        return self.kind

    @property
    def combat_weapons(self) -> list[Weapon]:
        return [w for w in self.weapons if w.usable]


class GameData:
    def __init__(self, rules: Ini, strings: dict[str, str] | None = None):
        self.rules = rules
        self.strings = strings or {}
        self.weapons: dict[str, Weapon] = {}
        self.technos: dict[str, Techno] = {}
        g = rules.section("General")
        # The engine keeps these as 32-bit floats: 0.7 becomes 0.69999998, which matters when the
        # build time lands exactly on a whole step (cost 600 -> 378 frames -> 6.9999 -> 6 steps).
        self.build_speed = _f32(float(g.get("BuildSpeed", "0.7")))
        self.multiple_factory = _f32(float(g.get("MultipleFactory", "0.8")))
        self.low_power_penalty = float(g.get("LowPowerPenaltyModifier", "1"))
        self.min_low_power_speed = float(g.get("MinLowPowerProductionSpeed", "0.5"))
        self.max_low_power_speed = float(g.get("MaxLowPowerProductionSpeed", "0.8"))
        self.generic_prereqs = {tok: rules.get_list("General", key) for tok, key in GENERIC_PREREQS.items()}
        self.generic_prereqs["PROC"] += rules.get_list("General", "PrerequisiteProcAlternate")
        self.ore_value = rules.get_int("Riparius", "Value", 25)
        for registry, kind in TECHNO_REGISTRIES.items():
            for tid in rules.registry(registry):
                if rules.section(tid) and tid not in self.technos:
                    self.technos[tid] = self._load_techno(tid, kind)

    @classmethod
    def load(cls, data_dir: str | Path) -> "GameData":
        data_dir = Path(data_dir)
        rules = Ini.from_bytes((data_dir / "rulesmd.ini").read_bytes())
        csf = data_dir / "ra2md.csf"
        strings = parse_csf(csf.read_bytes()) if csf.exists() else {}
        return cls(rules, strings)

    # ------------------------------------------------------------------ loading
    def weapon(self, wid: str | None) -> Weapon | None:
        if not wid or wid.lower() == "none" or wid not in self.rules:
            return None
        if wid in self.weapons:
            return self.weapons[wid]
        r = self.rules
        sec = r.section(wid)
        wh_name = sec.get("Warhead", "")
        wh = r.section(wh_name)
        proj = r.section(sec.get("Projectile", ""))
        unsupported = None
        for flag in UNSUPPORTED_WEAPON_FLAGS:
            if _yes(sec.get(flag)):
                unsupported = flag
        for flag in UNSUPPORTED_WARHEAD_FLAGS:
            if _yes(wh.get(flag)):
                unsupported = flag
        if not wh:
            unsupported = unsupported or "NoWarhead"
        verses = warhead_verses(r, wh_name) if wh else {a: 1.0 for a in ARMOR_TYPES}
        w = Weapon(
            id=wid,
            damage=r.get_float(wid, "Damage"),
            rof=max(1, r.get_int(wid, "ROF", 1)),
            range=r.get_float(wid, "Range"),
            min_range=r.get_float(wid, "MinimumRange"),
            burst=max(1, r.get_int(wid, "Burst", 1)),
            verses=np.array([verses[a] for a in ARMOR_TYPES]),
            aa=_yes(proj.get("AA", "no")),
            ag=_yes(proj.get("AG", "yes")),
            cell_spread=r.get_float(wh_name, "CellSpread") if wh else 0.0,
            percent_at_max=r.get_float(wh_name, "PercentAtMax", 1.0) if wh else 1.0,
            suicide=_yes(sec.get("Suicide")),
            unsupported=unsupported,
        )
        self.weapons[wid] = w
        return w

    def _load_techno(self, tid: str, kind: str) -> Techno:
        r = self.rules
        sec = r.section(tid)
        label = sec.get("UIName", "")
        armor = sec.get("Armor", "none").lower()
        if sec.get("Primary"):
            wids = [sec.get("Primary"), sec.get("Secondary")]
        elif r.get_int(tid, "WeaponCount"):
            # Gattling / multi-weapon units: use the last ground/air stage pair.
            n = r.get_int(tid, "WeaponCount")
            wids = [sec.get(f"Weapon{max(1, n - 1)}"), sec.get(f"Weapon{n}")] if n > 1 else [sec.get("Weapon1")]
        else:
            wids = []
        weapons = [w for w in (self.weapon(x) for x in wids) if w is not None]
        factory = sec.get("Factory")
        return Techno(
            id=tid,
            name=self.strings.get(label.upper(), sec.get("Name", tid)),
            kind=kind,
            cost=r.get_int(tid, "Cost"),
            strength=r.get_int(tid, "Strength"),
            armor=ARMOR_TYPES.index(armor) if armor in ARMOR_TYPES else 0,
            speed=r.get_int(tid, "Speed"),
            owners=set(r.get_list(tid, "Owner")),
            required_houses=set(r.get_list(tid, "RequiredHouses")),
            forbidden_houses=set(r.get_list(tid, "ForbiddenHouses")),
            prerequisites=r.get_list(tid, "Prerequisite"),
            prerequisite_override=r.get_list(tid, "PrerequisiteOverride"),
            techlevel=r.get_int(tid, "TechLevel", -1),
            build_time_mult=_f32(r.get_float(tid, "BuildTimeMultiplier", 1.0)),
            power=r.get_int(tid, "Power"),
            factory=FACTORY_CATEGORY.get(factory) if factory else None,
            weapons=weapons,
            ammo=r.get_int(tid, "Ammo", -1),
            naval=_yes(sec.get("Naval")) or sec.get("MovementZone", "") in ("Water", "AmphibiousCrusher"),
            free_unit=sec.get("FreeUnit"),
            storage=r.get_int(tid, "Storage"),
            teleporter=_yes(sec.get("Teleporter")),
            harvester=_yes(sec.get("Harvester")) or _yes(sec.get("ResourceGatherer")),
            enslaves=sec.get("Enslaves"),
            slaves_number=r.get_int(tid, "SlavesNumber"),
            build_limit=r.get_int(tid, "BuildLimit"),
            crusher=_yes(sec.get("Crusher")),
            flies=sec.get("MovementZone", "") == "Fly" or _yes(sec.get("ConsideredAircraft")),
            deploy_fire=_yes(sec.get("DeployFire")),
            requires_stolen_tech=any(_yes(sec.get(f"RequiresStolen{x}Tech")) for x in ("Allied", "Soviet", "Third")),
        )

    # ------------------------------------------------------------- availability
    @staticmethod
    def side_of(country: str) -> str:
        return COUNTRIES[country][0]

    def basics(self, country: str) -> dict[str, str]:
        return SIDE_BASICS[self.side_of(country)]

    def country_can_own(self, t: Techno, country: str) -> bool:
        if not 0 <= t.techlevel <= 10 or country not in t.owners or t.requires_stolen_tech:
            return False
        if t.required_houses and country not in t.required_houses:
            return False
        return country not in t.forbidden_houses

    def prereq_options(self, token: str) -> list[str]:
        """Buildings that satisfy one prerequisite token (generic tokens map to several)."""
        return self.generic_prereqs.get(token.upper(), [token])

    def prereqs_met(self, t: Techno, owned: set[str] | dict[str, int]) -> bool:
        if any(o in owned for o in t.prerequisite_override):
            return True
        return all(any(o in owned for o in self.prereq_options(tok)) for tok in t.prerequisites)

    def can_build(self, tid: str, country: str, owned: set[str] | dict[str, int]) -> bool:
        t = self.technos.get(tid)
        return t is not None and self.country_can_own(t, country) and self.prereqs_met(t, owned)

    def buildable_closure(self, country: str, owned: set[str] | None = None) -> set[str]:
        """Everything this country could eventually build starting from its construction yard."""
        owned = set(owned or {self.basics(country)["cnst"]})
        changed = True
        while changed:
            changed = False
            for t in self.technos.values():
                if t.id not in owned and t.kind == "building" and self.can_build(t.id, country, owned):
                    owned.add(t.id)
                    changed = True
        result = {t.id for t in self.technos.values() if self.can_build(t.id, country, owned)}
        return result | owned

    def combat_units(self, country: str, owned: set[str] | None = None,
                     include_air: bool = True) -> list[Techno]:
        """Mobile units the country can build that the combat simulator can model."""
        pool = self.buildable_closure(country) if owned is None else {
            t.id for t in self.technos.values() if self.can_build(t.id, country, owned)}
        units = []
        for tid in sorted(pool):
            t = self.technos[tid]
            if t.kind == "building" or t.naval or t.harvester or t.cost <= 0 or not t.combat_weapons:
                continue
            if t.is_air and not include_air:
                continue
            units.append(t)
        return units

    def build_frames(self, t: Techno, factories: int = 1) -> float:
        """Build time in frames at full power with `factories` factories of that kind."""
        raw = t.cost / 1000 * self.build_speed * 900 * t.build_time_mult
        raw *= self.multiple_factory ** max(factories - 1, 0)
        return max(int(raw / PRODUCTION_STEPS), 1) * PRODUCTION_STEPS
