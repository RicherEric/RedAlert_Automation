"""Send the army at the AI base until the AI is defeated (stage 6, live side of VictoryProblem).

Two phases. Gather: every unit moves to a rally point `gather_distance` cells short of the AI base
(on the side we come from) and the attack waits until `gather_ready` of the army is there, so fast
tanks do not arrive alone (the first live test vs the hard AI lost all its tanks that way). Attack:
YR 1.001 has no attack-move, so every unit gets an explicit ATTACK order: the weakest armed enemy
within `threat_range` cells of it (focus fire on whatever is shooting back: units, pillboxes),
else the construction yard (the AI rebuilds its defences as long as it has one), else the nearest
armed building, else the nearest building, else the nearest enemy object. Orders are re-issued when
a target dies or something more urgent comes close, and at most every `retarget_frames` per unit
otherwise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .bridge import BridgeError
from .gamedata import GameData
from .memory import ObjectState, Snapshot

NOT_ARMY = {"AMCV", "SMCV", "PCV"}
CONSTRUCTION_YARDS = {"GACNST", "NACNST", "YACNST"}
PARADROP = {"CAAIRP"}  # tech airport: its owner gets paratroopers


@dataclass
class Assault:
    db: GameData
    commander: object               # Bridge (attack(unit, target), move(unit, cell))
    enemy: int                      # the AI house index
    threat_range: float = 9.0
    retarget_frames: int = 60
    gather_distance: float = 22.0   # 0: no gathering, attack at once
    gather_radius: float = 6.0      # within this of the rally point counts as gathered
    gather_ready: float = 0.9       # share of the army that must be gathered
    gather_timeout: int = 2000      # frames, then attack with whoever is there
    # Second rally this close to the AI base (0: none). From the first rally every unit still
    # moved at its own speed and the Rhinos reached the base ~150 frames before the infantry
    # and died first (live vs medium, 2026-11-01).
    regroup_distance: float = 14.0
    # Unit types left out of this army: the home guard for the Assault, everyone else for the
    # home Defence while the army is away (see execute.py run_attack).
    exclude: frozenset = frozenset()
    only: frozenset | None = None
    started: int | None = None
    stage_started: int | None = None
    regrouping: bool = False
    attack_started: int | None = None
    rally: tuple[int, int] | None = None
    orders: int = 0
    _target: dict[int, int] = field(default_factory=dict)      # our unit -> target address
    _ordered_at: dict[int, int] = field(default_factory=dict)  # our unit -> frame of last order

    def army(self, snap: Snapshot) -> list[ObjectState]:
        out = []
        for o in snap.objects:
            t = self.db.technos.get(o.type_id)
            if (o.owner == snap.player_index and o.on_map and o.health > 0 and o.kind != "building"
                    and t is not None and t.combat_weapons and not t.harvester and o.type_id not in NOT_ARMY
                    and o.type_id not in self.exclude and (self.only is None or o.type_id in self.only)):
                out.append(o)
        return out

    def enemies(self, snap: Snapshot) -> list[ObjectState]:
        return [o for o in snap.objects if o.owner == self.enemy and o.on_map and o.health > 0]

    def defeated(self, snap: Snapshot) -> bool:
        house = next((h for h in snap.houses if h.index == self.enemy), None)
        if house is None:
            return False  # no house data (the match has ended): unknown, not a win
        if house.defeated:
            return True
        return not any(o.kind == "building" for o in self.enemies(snap))

    def _armed(self, o: ObjectState) -> bool:
        t = self.db.technos.get(o.type_id)
        return t is not None and bool(t.combat_weapons)

    def _dps(self, unit: ObjectState, target: ObjectState) -> float:
        """Damage per frame of `unit`'s best weapon against `target`'s armour (0: cannot hurt it)."""
        a, b = self.db.technos.get(unit.type_id), self.db.technos.get(target.type_id)
        if a is None or b is None:
            return 0.0
        return max((w.damage * w.burst * w.verses[b.armor] / max(w.rof, 1)
                    for w in a.combat_weapons if (w.aa if b.is_air else w.ag)), default=0.0)

    def _choose(self, unit: ObjectState, enemies: list[ObjectState]) -> ObjectState | None:
        if not enemies:
            return None
        d = lambda o: math.dist((unit.x, unit.y), (o.x, o.y))
        threats = [o for o in enemies if self._armed(o) and d(o) <= self.threat_range]
        hurt = [(o, self._dps(unit, o)) for o in threats]
        hurt = [(o, dps) for o, dps in hurt if dps > 0]
        if hurt:
            # Most enemy value destroyed per frame (cost over time to kill): Tesla Troopers take
            # on tanks, conscripts the infantry. Lowest raw health sent everyone at the GIs while
            # the Grizzlies crushed our infantry (live vs medium, 2026-11-01).
            value = lambda o: self.db.technos[o.type_id].cost or 100
            return max(hurt, key=lambda p: (value(p[0]) * p[1] / max(p[0].health, 1), -d(p[0])))[0]
        if threats:
            return min(threats, key=lambda o: (o.health, d(o)))
        buildings = [o for o in enemies if o.kind == "building"]
        tech = lambda o: self.db.technos.get(o.type_id)
        # Power plants first while powered defences stand (Prism Towers, Patriots go offline
        # without power: guides' "power cutting"); then what makes and drops units: factories,
        # and the tech airport, whose paratroopers raided our empty base (live vs medium).
        powered = any(self._armed(o) and (tech(o).power or 0) < 0 for o in buildings)
        power = [o for o in buildings if (tech(o).power or 0) > 0] if powered else []
        makers = [o for o in buildings if tech(o).factory or o.type_id in PARADROP]
        for group in ([o for o in buildings if o.type_id in CONSTRUCTION_YARDS], power, makers,
                      [o for o in buildings if self._armed(o)], buildings, enemies):
            if group:
                return min(group, key=d)
        return None

    def _rally_point(self, army: list[ObjectState], enemies: list[ObjectState],
                     distance: float | None = None) -> tuple[int, int] | None:
        """`distance` (default gather_distance) cells from the AI base centre towards our army."""
        distance = self.gather_distance if distance is None else distance
        pts = [(o.x, o.y) for o in enemies if o.kind == "building"] or [(o.x, o.y) for o in enemies]
        if not pts or not army:
            return None
        bx, by = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        ax, ay = sum(o.x for o in army) / len(army), sum(o.y for o in army) / len(army)
        dist = math.dist((ax, ay), (bx, by))
        if dist <= distance:
            return None  # already that close: nothing to gather for
        k = distance / dist
        return round(bx + (ax - bx) * k), round(by + (ay - by) * k)

    def _gather(self, snap: Snapshot, army: list[ObjectState], enemies: list[ObjectState]) -> bool:
        """Move to the rally point; True once the attack may start."""
        if self.gather_distance <= 0:
            return True
        if self.stage_started is None:
            self.stage_started = snap.frame
        if self.rally is None:
            self.rally = self._rally_point(army, enemies)
            if self.rally is None:
                return True
        near = lambda o: math.dist((o.x, o.y), self.rally) <= self.gather_radius
        engaged = any(self._armed(e) and math.dist((u.x, u.y), (e.x, e.y)) <= self.threat_range
                      for u in army for e in enemies)
        if engaged:
            return True
        # By value: 20 conscripts at the rally are not the army while the Kirovs are still far
        # behind (live vs medium: the ground went in alone, the Kirovs arrived 2000+ frames later).
        cost = lambda o: self.db.technos[o.type_id].cost or 1
        if (sum(cost(o) for o in army if near(o)) >= self.gather_ready * sum(map(cost, army))
                or snap.frame - self.stage_started >= self.gather_timeout):
            if self.regrouping or self.regroup_distance <= 0:
                return True
            nxt = self._rally_point(army, enemies, self.regroup_distance)
            if nxt is None:
                return True
            self.regrouping, self.rally, self.stage_started = True, nxt, snap.frame
            self._ordered_at.clear()  # and order everyone on to it below
        for unit in army:
            if near(unit):
                continue
            if snap.frame - self._ordered_at.get(unit.address, -10**9) < 300:
                continue
            try:
                self.commander.move(unit.address, self.rally)
            except BridgeError:
                continue
            self._ordered_at[unit.address] = snap.frame
            self.orders += 1
        return False

    def tick(self, snap: Snapshot) -> None:
        if self.started is None:
            self.started = snap.frame
        enemies = self.enemies(snap)
        army = self.army(snap)
        if self.attack_started is None:
            if not self._gather(snap, army, enemies):
                return
            self.attack_started = snap.frame
            self._ordered_at.clear()
        self._engage(snap, army, enemies)

    def _engage(self, snap: Snapshot, army: list[ObjectState], enemies: list[ObjectState]) -> None:
        alive = {o.address for o in enemies}
        for unit in army:
            current = self._target.get(unit.address)
            best = self._choose(unit, enemies)
            if best is None:
                return
            stale = snap.frame - self._ordered_at.get(unit.address, -10**9) >= self.retarget_frames
            urgent = best.address != current and self._armed(best) and current is not None \
                and current in alive and not self._armed(next(o for o in enemies if o.address == current))
            if current in alive and not urgent and not (stale and best.address != current):
                continue
            try:
                self.commander.attack(unit.address, best.address)
            except BridgeError:
                continue  # the unit or target died between the snapshot and the order
            self._target[unit.address] = best.address
            self._ordered_at[unit.address] = snap.frame
            self.orders += 1


SABOTEURS = {"TANY", "GHOST", "ENGINEER", "SENGINEER", "YENGINEER", "SPY"}  # C4 / capture / infiltrate


@dataclass
class Defence(Assault):
    """While the army is still being built: AI combat units (and saboteurs) that come within
    `radius` cells of our base (the centre of our buildings) are fought by our combat units.
    Under ShortGame rules losing the buildings loses the game, and idle units only shoot back at
    what attacks them (the hard AI's first wave razed the base that way, 2026-11-01).

    Live vs hard (2026-11-02) a Nighthawk drew the whole army 11 cells away while it dropped
    Tanya next to the construction yard, whose C4 took the yard and the refinery in 40 frames.
    So: a unit only goes after threats it can hurt (no chasing aircraft it cannot shoot);
    saboteurs and threats close to our buildings come first; the `yard_guard` units nearest the
    construction yard stay within `guard_radius` of it; and with nothing to fight, units that
    wandered more than `return_distance` from home walk back."""
    radius: float = 25.0
    yard_guard: int = 4
    guard_radius: float = 6.0
    return_distance: float = 12.0
    _guards: set = field(default_factory=set)
    _buildings: list = field(default_factory=list)

    def _home(self, snap: Snapshot) -> tuple[float, float] | None:
        ours = [(o.x, o.y) for o in snap.objects
                if o.owner == snap.player_index and o.on_map and o.health > 0 and o.kind == "building"]
        if not ours:
            return None
        return sum(p[0] for p in ours) / len(ours), sum(p[1] for p in ours) / len(ours)

    def _yard(self, snap: Snapshot) -> ObjectState | None:
        return next((o for o in snap.objects if o.owner == snap.player_index and o.on_map and o.health > 0
                     and o.type_id in CONSTRUCTION_YARDS), None)

    def threats(self, snap: Snapshot) -> list[ObjectState]:
        home = self._home(snap)
        if home is None:
            return []
        return [o for o in self.enemies(snap) if o.kind != "building"
                and (self._armed(o) or o.type_id in SABOTEURS) and math.dist((o.x, o.y), home) <= self.radius]

    def _choose(self, unit: ObjectState, threats: list[ObjectState]) -> ObjectState | None:
        ours = self._buildings
        hurt = [(o, self._dps(unit, o)) for o in threats]
        hurt = [(o, dps) for o, dps in hurt if dps > 0]
        if not hurt:
            return None  # e.g. a tank under a Nighthawk: stay put instead of chasing it
        near = lambda o: min((math.dist((o.x, o.y), b) for b in ours), default=0.0)
        value = lambda o: self.db.technos[o.type_id].cost or 100
        score = lambda p: (value(p[0]) * p[1] / max(p[0].health, 1) / (1 + near(p[0]) / 5)
                           * (10 if p[0].type_id in SABOTEURS else 1))
        return max(hurt, key=lambda p: (score(p), -math.dist((unit.x, unit.y), (p[0].x, p[0].y))))[0]

    def _move(self, snap: Snapshot, unit: ObjectState, cell: tuple[float, float]) -> None:
        if snap.frame - self._ordered_at.get(unit.address, -10**9) < 300:
            return
        try:
            self.commander.move(unit.address, (round(cell[0]), round(cell[1])))
        except BridgeError:
            return
        self._target.pop(unit.address, None)
        self._ordered_at[unit.address] = snap.frame
        self.orders += 1

    def tick(self, snap: Snapshot) -> None:
        home = self._home(snap)
        if home is None:
            return
        self._buildings = [(o.x, o.y) for o in snap.objects if o.owner == snap.player_index and o.on_map
                           and o.health > 0 and o.kind == "building"]
        army = self.army(snap)
        threats = self.threats(snap)
        yard = self._yard(snap)
        guards: list[ObjectState] = []
        if yard is not None and self.yard_guard > 0:
            ground = [o for o in army if not self.db.technos[o.type_id].is_air]
            alive = {o.address for o in ground}
            self._guards &= alive
            for o in sorted(ground, key=lambda o: math.dist((o.x, o.y), (yard.x, yard.y))):
                if len(self._guards) >= self.yard_guard:
                    break
                self._guards.add(o.address)
            guards = [o for o in ground if o.address in self._guards]
            at_yard = [t for t in threats if math.dist((t.x, t.y), (yard.x, yard.y)) <= self.guard_radius + 4]
            self._engage(snap, guards, at_yard)
            for g in guards:
                if g.address not in self._target and math.dist((g.x, g.y), (yard.x, yard.y)) > self.guard_radius:
                    self._move(snap, g, (yard.x, yard.y))
        rest = [o for o in army if o.address not in self._guards] if guards else army
        if threats:
            self._engage(snap, rest, threats)
        for o in rest:
            if (o.address not in self._target or not threats) and math.dist((o.x, o.y), home) > self.return_distance:
                self._move(snap, o, home)

    def _engage(self, snap: Snapshot, army: list[ObjectState], enemies: list[ObjectState]) -> None:
        """Like Assault._engage, but a unit with nothing it can hurt drops its target."""
        alive = {o.address for o in enemies}
        for unit in army:
            best = self._choose(unit, enemies)
            current = self._target.get(unit.address)
            if best is None:
                if current is not None and current not in alive:
                    self._target.pop(unit.address, None)
                continue
            if current == best.address:
                continue
            stale = snap.frame - self._ordered_at.get(unit.address, -10**9) >= self.retarget_frames
            if current in alive and not stale:
                cur = next(o for o in enemies if o.address == current)
                if not (best.type_id in SABOTEURS and cur.type_id not in SABOTEURS):
                    continue
            try:
                self.commander.attack(unit.address, best.address)
            except BridgeError:
                continue
            self._target[unit.address] = best.address
            self._ordered_at[unit.address] = snap.frame
            self.orders += 1
