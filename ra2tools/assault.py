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

NOT_ARMY = {"AMCV", "SMCV", "PCV", "BPLN"}  # BPLN: the MiGs Boris calls in, not ours to command
CONSTRUCTION_YARDS = {"GACNST", "NACNST", "YACNST"}
PARADROP = {"CAAIRP"}  # tech airport: its owner gets paratroopers
AIRSTRIKE = {"Flare"}  # Boris's laser: on a building it calls in MiGs (damage 1 in the tables)
# Superweapons: the Weather Controller's storm razed our tech, radar, barracks and power in 150
# frames (series_v10 game 2) and the Chronosphere drops tanks in our base; razing them stops both.
SUPERWEAPONS = {"GAWEAT", "GACSPH", "NAMISL", "NAIRON", "YAGNTC", "YAPPET"}
ARTILLERY_RANGE = 14.0  # a unit outranging this (V3, 18 cells) shells buildings from beyond the Prism Towers (8)


ENGAGED_SHARE = 0.5  # share of the army (by cost) at the rally before a fight there starts the attack
# Live series_v11 game 1 (2026-11-09): after the order to gather, the 17 vehicles stayed at home
# for 3800 frames although a MOVE went to each every 300 frames, while the infantry and Boris
# walked ahead alone and lost 11 units before the rally. Bridge errors were dropped unlogged
# (MOVE answers "ERR event queue full" when the game's event list is full), so now every MOVE
# result goes to `move_log`, a unit that has not moved for STUCK_FRAMES is sent to a free cell
# beside it first, and units more than GATHER_LEAD cells ahead of the army hold.
STUCK_FRAMES = 600
STUCK_MOVE = 1.0     # cells: less than this since its order counts as not moving
GATHER_LEAD = 10.0   # cells ahead of the army's (value-weighted) median distance to the rally
# Wait at the rally for an opening (2026-11-10): over 40 live first waves (series_v6-v12) the
# battle sim could not tell the 11 won from the lost, but where the AI army stood could: our army
# value over the AI's armed units within OPENING_RADIUS of its construction yard: under 1.65 only
# 1 of 13 first waves won, at 2 or more 7 of 19 (under 2: 4 of 21).
OPENING_RADIUS = 20.0

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
    skip: set = field(default_factory=set)   # unit addresses another controller uses (Steward.claimed)
    extra: set = field(default_factory=set)  # unit addresses in this army whatever their type (reinforcements)
    # Frames-of-fire horizon for focus fire: a target the units already on it kill within this
    # many frames gets no more shooters (overkill); one they are still working on draws more.
    focus_frames: int = 45
    # During the attack a ground unit this many cells nearer the AI base than the slowest ground
    # units, with nothing in its own range, falls back to them (0: off). Live 2026-11-06 (game
    # 14) the Rhinos and flak tracks drove 4-8 cells ahead of the Apocalypses into the Prism
    # Towers and died before the Apocalypses fired.
    pace_lead: float = 3.0
    # A designator (Boris) shoots back only at enemy units this close that can hurt him; else he
    # lasers buildings for airstrikes. Live series_v7 his rifle duelled GIs and pillboxes while
    # the MiGs came only when nothing else was left to shoot.
    designator_guard: float = 6.0
    # At the first rally, wait until our army is worth opening_ratio x the AI army at home (0: go
    # at once), at most opening_wait frames: the AI grows meanwhile (sim: each 1000 frames later wins less).
    opening_ratio: float = 2.0
    opening_wait: int = 3000
    opening: dict | None = None     # how the wait ended: frame, ratio, ours, ai_home, waited, reason
    _wait_from: int | None = None
    order_log: list = field(default_factory=list)  # (frame, unit type, target type, x, y) per attack order
    # (frame, unit type, x, y, "ok" | bridge error, why, unit address) per MOVE; why: rally, hold, aside, defend
    move_log: list = field(default_factory=list)
    started: int | None = None
    stage_started: int | None = None
    regrouping: bool = False
    attack_started: int | None = None
    rally: tuple[int, int] | None = None
    _progress: tuple = (-10**9, float("inf"))  # (frame, cost-weighted distance still to go) for the timeout
    orders: int = 0
    _target: dict[int, int] = field(default_factory=dict)      # our unit -> target address
    _ordered_at: dict[int, int] = field(default_factory=dict)  # our unit -> frame of last order
    _commit: dict[int, float] = field(default_factory=dict)    # enemy -> damage per frame aimed at it
    _moved_from: dict[int, tuple] = field(default_factory=dict)  # our unit -> (frame, x, y) of its gather order
    _unstick: dict[int, int] = field(default_factory=dict)       # our unit -> times sent aside

    def army(self, snap: Snapshot) -> list[ObjectState]:
        out = []
        for o in snap.objects:
            t = self.db.technos.get(o.type_id)
            if (o.owner == snap.player_index and o.on_map and o.health > 0 and o.kind != "building"
                    and t is not None and t.combat_weapons and not t.harvester and o.type_id not in NOT_ARMY
                    and o.address not in self.skip
                    and (o.address in self.extra
                         or (o.type_id not in self.exclude and (self.only is None or o.type_id in self.only)))):
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

    def _fighter(self, o: ObjectState) -> bool:
        """Armed and not a free support plane passing over (paradrop, spy plane: cost 0). Live
        2026-11-06 a paradrop plane flying past the rally point started the attack early."""
        t = self.db.technos.get(o.type_id)
        return self._armed(o) and not (t.kind == "aircraft" and not t.cost)

    def _dps(self, unit: ObjectState, target: ObjectState) -> float:
        """Damage per frame of `unit`'s best weapon against `target`'s armour (0: cannot hurt it)."""
        a, b = self.db.technos.get(unit.type_id), self.db.technos.get(target.type_id)
        if a is None or b is None:
            return 0.0
        return max((w.damage * w.burst * w.verses[b.armor] / max(w.rof, 1)
                    for w in a.combat_weapons if (w.aa if b.is_air else w.ag)), default=0.0)

    def _focus(self, unit: ObjectState, o: ObjectState, dps: float) -> float:
        """Enemy value destroyed per frame if `unit` joins the units already shooting `o`
        (focus fire), cut to a quarter when those already kill it within focus_frames."""
        others = self._commit.get(o.address, 0.0) - (dps if self._target.get(unit.address) == o.address else 0.0)
        value = self.db.technos[o.type_id].cost or 100
        # ×4 for what can shoot back at this unit (as the battle sim scores armed targets): live
        # 2026-11-06 the Kirovs bombed riflemen while Patriots and GIs brought them down.
        score = value * (dps + others) / max(o.health, 1) * (4.0 if self._dps(o, unit) > 0 else 1.0)
        return score * 0.25 if others * self.focus_frames >= o.health * 1.2 else score

    def _commitments(self, army: list[ObjectState], enemies: list[ObjectState]) -> None:
        by = {o.address: o for o in enemies}
        self._commit = {}
        for u in army:
            t = by.get(self._target.get(u.address))
            if t is not None:
                self._commit[t.address] = self._commit.get(t.address, 0.0) + self._dps(u, t)

    def bomber(self, type_id: str) -> bool:
        """A slow airship that drops bombs straight down (Kirov): it never gets over a unit that moves."""
        t = self.db.technos.get(type_id)
        return t is not None and t.is_air and bool(t.combat_weapons) and max(w.range for w in t.combat_weapons) <= 2

    def designator(self, type_id: str) -> bool:
        t = self.db.technos.get(type_id)
        return t is not None and any(w.id in AIRSTRIKE for w in t.weapons)

    def _airstrike(self, unit: ObjectState, enemies: list[ObjectState]) -> ObjectState | None:
        """The building a designator should laser, or None when he must fight for himself.
        The laser (12 cells) outranges the Prism Towers, so the defences come first: the MiGs
        take them out ahead of the army. The game walks him into laser range, no closer."""
        d = lambda o: math.dist((unit.x, unit.y), (o.x, o.y))
        if any(o.kind != "building" and d(o) <= self.designator_guard and self._dps(o, unit) > 0
               for o in enemies):
            return None
        buildings = [o for o in enemies if o.kind == "building"]
        reach = max((w.range for w in self.db.technos[unit.type_id].weapons if w.id in AIRSTRIKE), default=0.0)
        for group in ([o for o in buildings if o.type_id in SUPERWEAPONS and d(o) <= reach + 4],
                      [o for o in buildings if self._fighter(o)],
                      [o for o in buildings if o.type_id in CONSTRUCTION_YARDS], buildings):
            if group:
                return min(group, key=d)
        return None

    def _choose(self, unit: ObjectState, enemies: list[ObjectState]) -> ObjectState | None:
        if not enemies:
            return None
        d = lambda o: math.dist((unit.x, unit.y), (o.x, o.y))
        if self.bomber(unit.type_id) and any(o.kind == "building" for o in enemies):
            # Buildings only, the nearest that shoots back at it first: live 2026-11-06 the Kirovs
            # chased tanks and GIs across the AI base and razed nothing while Patriots shot them down.
            enemies = [o for o in enemies if o.kind == "building"]
            near = [o for o in enemies if d(o) <= self.threat_range + 4]
            for group in ([o for o in near if self._dps(o, unit) > 0], [o for o in near if self._armed(o)]):
                if group:
                    return min(group, key=d)
        reach = self._range(unit, next((o for o in enemies if o.kind == "building"), enemies[0]))
        if reach >= ARTILLERY_RANGE:
            # Artillery (V3) shells a superweapon in range first, then the defences it outranges.
            for group in ([o for o in enemies if o.type_id in SUPERWEAPONS],
                          [o for o in enemies if o.kind == "building" and self._armed(o)]):
                group = [o for o in group if d(o) <= reach]
                if group:
                    return min(group, key=d)
        threats = [o for o in enemies if self._armed(o) and d(o) <= self.threat_range]
        hurt = [(o, self._dps(unit, o)) for o in threats]
        hurt = [(o, dps) for o, dps in hurt if dps > 0]
        if hurt:
            # Most enemy value destroyed per frame (cost over time to kill): Tesla Troopers take
            # on tanks, conscripts the infantry. Lowest raw health sent everyone at the GIs while
            # the Grizzlies crushed our infantry (live vs medium, 2026-11-01). Counting the
            # damage of the units already on a target makes them finish it together.
            return max(hurt, key=lambda p: (self._focus(unit, p[0], p[1]), -d(p[0])))[0]
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
        for group in ([o for o in buildings if o.type_id in CONSTRUCTION_YARDS],
                      [o for o in buildings if o.type_id in SUPERWEAPONS], power, makers,
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
        # Back off (up to 16 cells) out of reach of the AI's guns: live 2026-11-06 a Tesla coil
        # 14 cells from the rally point shot at whoever arrived first.
        guns = [(e.x, e.y) for e in enemies if e.kind == "building" and self._fighter(e)]
        point = lambda d: (bx + (ax - bx) * d / dist, by + (ay - by) * d / dist)
        reach = self.threat_range + self.gather_radius + 2
        limit = min(distance + 16, dist - 5)
        while distance + 4 <= limit and any(math.dist(point(distance), p) <= reach for p in guns):
            distance += 4
        x, y = point(distance)
        return round(x), round(y)

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
        # By value: 20 conscripts at the rally are not the army while the Kirovs are still far
        # behind (live vs medium: the ground went in alone, the Kirovs arrived 2000+ frames later).
        cost = lambda o: self.db.technos[o.type_id].cost or 1
        # A fight at the rally starts the attack only once most of the army is there: live
        # 2026-11-06 a raider at our base, a paradrop plane or a Tesla coil near the rally each
        # started it with the first fast units, the Kirovs 5000 frames behind (games 4-9).
        engaged = any(self._fighter(e) and math.dist((u.x, u.y), (e.x, e.y)) <= self.threat_range
                      for u in army if near(u) for e in enemies)
        if engaged and sum(cost(o) for o in army if near(o)) >= ENGAGED_SHARE * sum(map(cost, army)):
            return True
        # The timeout counts from the last progress: slow Kirovs still closing in are waited for.
        behind = sum(cost(o) * math.dist((o.x, o.y), self.rally) for o in army if not near(o))
        if snap.frame - self._progress[0] >= 300:
            if behind < 0.95 * self._progress[1]:
                self.stage_started = snap.frame
            self._progress = (snap.frame, behind)
        if ((sum(cost(o) for o in army if near(o)) >= self.gather_ready * sum(map(cost, army))
                or snap.frame - self.stage_started >= self.gather_timeout)
                and (self.regrouping or self._opening(snap, army, enemies, cost))):
            if self.regrouping or self.regroup_distance <= 0:
                return True
            nxt = self._rally_point(army, enemies, self.regroup_distance)
            if nxt is None:
                return True
            self.regrouping, self.rally, self.stage_started = True, nxt, snap.frame
            self._progress = (snap.frame, float("inf"))
            self._ordered_at.clear()  # and order everyone on to it below
        lead = self._lead_limit(army, cost)
        for unit in army:
            if near(unit):
                self._moved_from.pop(unit.address, None)
                continue
            pos = (unit.x, unit.y)
            seen = self._moved_from.get(unit.address)
            if seen is None or math.dist(pos, seen[1:]) >= STUCK_MOVE:
                self._moved_from[unit.address] = seen = (snap.frame, *pos)
            ahead = lead is not None and math.dist(pos, self.rally) < lead
            if ahead:
                self._moved_from[unit.address] = (snap.frame, *pos)  # waiting is not being stuck
            if snap.frame - self._ordered_at.get(unit.address, -10**9) < 300:
                continue
            if ahead:
                cell, why = (round(unit.x), round(unit.y)), "hold"  # until the army catches up
            elif snap.frame - seen[0] >= STUCK_FRAMES:
                cell, why = self._aside(unit), "aside"
                self._moved_from[unit.address] = (snap.frame, *pos)
            else:
                cell, why = self.rally, "rally"
            if self._order_move(snap, unit, cell, why):
                self._ordered_at[unit.address] = snap.frame
        return False

    def ai_home_value(self, enemies: list[ObjectState]) -> float:
        """Cost of the AI's armed units (not buildings, not harvesters) near its construction
        yard, else near the centre of its buildings."""
        yard = next((o for o in enemies if o.type_id in CONSTRUCTION_YARDS), None)
        pts = [(o.x, o.y) for o in enemies if o.kind == "building"]
        if yard is not None:
            home = (yard.x, yard.y)
        elif pts:
            home = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        else:
            return 0.0
        total = 0.0
        for o in enemies:
            t = self.db.technos.get(o.type_id)
            if (o.kind != "building" and self._fighter(o) and not t.harvester
                    and math.dist((o.x, o.y), home) <= OPENING_RADIUS):
                total += t.cost or 0
        return total

    def _opening(self, snap: Snapshot, army: list[ObjectState], enemies: list[ObjectState], cost) -> bool:
        """Gathered at the first rally: go now? Yes once our army is worth opening_ratio x the
        AI army at home, or after opening_wait frames of waiting."""
        if self.opening_ratio <= 0:
            return True
        if self._wait_from is None:
            self._wait_from = snap.frame
        ours, theirs = float(sum(map(cost, army))), self.ai_home_value(enemies)
        ratio = ours / theirs if theirs else math.inf
        waited = snap.frame - self._wait_from
        if ratio >= self.opening_ratio:
            reason = "ratio"
        elif waited >= self.opening_wait:
            reason = "timeout"
        else:
            return False
        self.opening = {"frame": snap.frame, "ratio": round(ratio, 2), "ours": ours, "ai_home": theirs,
                        "waited": waited, "reason": reason}
        return True

    def _lead_limit(self, army: list[ObjectState], cost) -> float | None:
        """Distance to the rally under which a unit is too far ahead of the army and waits: the
        value-weighted median distance minus GATHER_LEAD (None with fewer than 2 units)."""
        if len(army) < 2:
            return None
        dists = sorted((math.dist((o.x, o.y), self.rally), cost(o)) for o in army)
        half, acc = sum(c for _, c in dists) / 2, 0.0
        for d, c in dists:
            acc += c
            if acc >= half:
                return d - GATHER_LEAD
        return None

    def _aside(self, unit: ObjectState) -> tuple[int, int]:
        """A cell 3 to the side of a stuck unit (alternating sides) and 2 towards the rally."""
        n = self._unstick.get(unit.address, 0)
        self._unstick[unit.address] = n + 1
        dx, dy = self.rally[0] - unit.x, self.rally[1] - unit.y
        norm = math.hypot(dx, dy) or 1.0
        dx, dy = dx / norm, dy / norm
        side = 1 if n % 2 == 0 else -1
        return round(unit.x - dy * 3 * side + dx * 2), round(unit.y + dx * 3 * side + dy * 2)

    def _order_move(self, snap: Snapshot, unit: ObjectState, cell: tuple[int, int], why: str = "defend") -> bool:
        """MOVE through the bridge, the result (ok or the bridge's error) kept in move_log."""
        try:
            self.commander.move(unit.address, cell)
        except BridgeError as e:
            self.move_log.append((snap.frame, unit.type_id, cell[0], cell[1], str(e), why, unit.address))
            return False
        self.move_log.append((snap.frame, unit.type_id, cell[0], cell[1], "ok", why, unit.address))
        self.orders += 1
        return True

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

    def _core(self, army: list[ObjectState], enemies: list[ObjectState]):
        """(base centre, distance of the slowest ground units to it, their centre), or None."""
        pts = [(o.x, o.y) for o in enemies if o.kind == "building"]
        ground = [o for o in army if not self.db.technos[o.type_id].is_air]
        if self.pace_lead <= 0 or not pts or len(ground) < 2:
            return None
        base = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        # Stragglers left far behind (units still at home) are not the pace: live 2026-11-07
        # conscripts back at the base put the "slow group" 45 cells out.
        far = sorted(math.dist((o.x, o.y), base) for o in ground)[len(ground) // 2] + 20
        ground = [o for o in ground if math.dist((o.x, o.y), base) <= far]
        slow = min(self.db.technos[o.type_id].speed for o in ground)
        core = [o for o in ground if self.db.technos[o.type_id].speed <= slow + 1]
        if len(core) == len(ground):
            return None
        d = sorted(math.dist((o.x, o.y), base) for o in core)[len(core) // 2]
        return base, d, (sum(o.x for o in core) / len(core), sum(o.y for o in core) / len(core))

    def _in_reach(self, unit: ObjectState, enemies: list[ObjectState]) -> list[ObjectState]:
        """Enemies `unit` can hurt without moving (range + 1 of a weapon that hits them)."""
        return [e for e in enemies if math.dist((unit.x, unit.y), (e.x, e.y)) <= self._range(unit, e) + 1]

    def _range(self, a: ObjectState, b: ObjectState) -> float:
        """Longest range of `a`'s weapons that can hurt `b` (-1: none). A flak track's AA gun
        reaches 10 cells, its ground gun 5."""
        ta, tb = self.db.technos.get(a.type_id), self.db.technos.get(b.type_id)
        if ta is None or tb is None:
            return -1.0
        return max((w.range for w in ta.combat_weapons
                    if (w.aa if tb.is_air else w.ag) and w.damage * w.verses[tb.armor] > 0), default=-1.0)

    def _under_towers(self, unit: ObjectState, enemies: list[ObjectState]) -> bool:
        """Within range (+1) of an enemy defence building that can hurt `unit`."""
        return any(e.kind == "building" and math.dist((unit.x, unit.y), (e.x, e.y)) <= self._range(e, unit) + 1
                   for e in enemies if self._range(e, unit) >= 0)

    def _engage(self, snap: Snapshot, army: list[ObjectState], enemies: list[ObjectState]) -> None:
        alive = {o.address for o in enemies}
        self._commitments(army, enemies)
        core = self._core(army, enemies)
        for unit in army:
            current = self._target.get(unit.address)
            strike = self._airstrike(unit, enemies) if self.designator(unit.type_id) else None
            if strike is not None:
                if strike.address != current:  # a new order would restart the laser
                    try:
                        self.commander.attack(unit.address, strike.address)
                    except BridgeError:
                        continue
                    self._target[unit.address] = strike.address
                    self._ordered_at[unit.address] = snap.frame
                    self.orders += 1
                    self.order_log.append((snap.frame, unit.type_id, strike.type_id, round(strike.x), round(strike.y)))
                continue
            if core is not None and not self.db.technos[unit.type_id].is_air:
                base, d, centre = core
                reach = self._in_reach(unit, enemies)
                ahead = math.dist((unit.x, unit.y), base) < d - self.pace_lead
                if ahead and reach and not self._under_towers(unit, enemies):
                    # Shoot only what is already in range, no chasing: live 2026-11-07 the flak
                    # tracks chased Harriers and Grizzlies into the Prism Towers, 6 of 7 lost
                    # before the Apocalypses arrived.
                    best = max(reach, key=lambda o: (self._focus(unit, o, self._dps(unit, o)),
                                                     -math.dist((unit.x, unit.y), (o.x, o.y))))
                    if best.address != current:
                        try:
                            self.commander.attack(unit.address, best.address)
                        except BridgeError:
                            continue
                        self._target[unit.address] = best.address
                        self._ordered_at[unit.address] = snap.frame
                        self.orders += 1
                        self.order_log.append((snap.frame, unit.type_id, best.type_id, round(best.x), round(best.y)))
                    continue
                if ahead:
                    if self._target.get(unit.address) != -1 or \
                            snap.frame - self._ordered_at.get(unit.address, -10**9) >= self.retarget_frames:
                        try:
                            self.commander.move(unit.address, (round(centre[0]), round(centre[1])))
                        except BridgeError:
                            continue
                        self._target[unit.address] = -1  # holding for the slow ones
                        self._ordered_at[unit.address] = snap.frame
                        self.orders += 1
                    continue
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
            self.order_log.append((snap.frame, unit.type_id, best.type_id, round(best.x), round(best.y)))


# C4 / capture / infiltrate / erase. The Chrono Legionnaire erases a building outright: live
# 2026-11-11 (series_v13 game 3) two of them took our construction yard (962/1000 HP) in 900
# frames while the guards shot the Grizzlies next to them, and with it went any rebuilding.
SABOTEURS = {"TANY", "GHOST", "ENGINEER", "SENGINEER", "YENGINEER", "SPY", "CLEG"}


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
                and (self._fighter(o) or o.type_id in SABOTEURS) and math.dist((o.x, o.y), home) <= self.radius]

    def _choose(self, unit: ObjectState, threats: list[ObjectState]) -> ObjectState | None:
        ours = self._buildings
        hurt = [(o, self._dps(unit, o)) for o in threats]
        hurt = [(o, dps) for o, dps in hurt if dps > 0]
        if not hurt:
            return None  # e.g. a tank under a Nighthawk: stay put instead of chasing it
        near = lambda o: min((math.dist((o.x, o.y), b) for b in ours), default=0.0)
        score = lambda p: (self._focus(unit, p[0], p[1]) / (1 + near(p[0]) / 5)
                           * (10 if p[0].type_id in SABOTEURS else 1))
        return max(hurt, key=lambda p: (score(p), -math.dist((unit.x, unit.y), (p[0].x, p[0].y))))[0]

    def _move(self, snap: Snapshot, unit: ObjectState, cell: tuple[float, float]) -> None:
        if snap.frame - self._ordered_at.get(unit.address, -10**9) < 300:
            return
        if not self._order_move(snap, unit, (round(cell[0]), round(cell[1]))):
            return
        self._target.pop(unit.address, None)
        self._ordered_at[unit.address] = snap.frame

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
        self._commitments(army, enemies)
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
