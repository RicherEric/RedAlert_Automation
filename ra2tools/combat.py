"""Simplified open-field battle simulator (numpy, fixed time step).

Modelled: HP, armor multipliers, range/minimum range, movement, rate of fire, burst,
splash (CellSpread/PercentAtMax), AA/AG targeting, suicide weapons, aircraft ammo + rearm trips,
crushing infantry, deploy-to-fire weapons (GI, Guardian GI, Siege Chopper lands), optional kiting,
optional focus fire for side A (our controller, assault.py: most value killed per frame).
Not modelled: terrain, pathfinding, collisions, veterancy, projectile flight time,
special mechanics (mind control, chrono, terror drones, ...).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gamedata import GameData
from .params import Calibration
from .rules import ARMOR_TYPES


@dataclass
class CombatResult:
    frames: int
    winner: str  # "A", "B" or "draw"
    a_value_left: float  # HP-weighted share of side A's cost still alive (0..1)
    b_value_left: float
    a_survivors: dict[str, int]
    b_survivors: dict[str, int]

    @property
    def score(self) -> float:
        """+1 = A wins untouched, -1 = B wins untouched."""
        return self.a_value_left - self.b_value_left


def _formation(n: int, x0: float, direction: float, spacing: float) -> np.ndarray:
    cols = max(1, int(np.ceil(np.sqrt(n))))
    idx = np.arange(n)
    x = x0 + direction * (idx // cols) * spacing
    y = (idx % cols - (cols - 1) / 2) * spacing
    return np.stack([x, y], axis=1)


class Battle:
    """Precomputes the static damage tables for one matchup; run() can be called repeatedly.

    Side B can be a base that keeps working during the fight (VictoryProblem):
      spawns   [(frame, type)] B units/buildings that appear at that frame, if the building that
               produces them (barracks, war factory, construction yard, ...) is still standing
      rebuild  B losses are made again, one at a time per factory kind at their build time:
               buildings while a B construction yard stands (the AI rebuilds its pillboxes),
               units while their barracks / war factory / ... stands. Recorded attacks on the
               AI showed it replacing $1200-2700 of units within 2000 frames, which a timeline
               recorded while nobody attacked it never shows (2026-11-01)
    """

    def __init__(self, db: GameData, army_a: dict[str, int], army_b: dict[str, int],
                 cal: Calibration | None = None, distance: float = 12.0, spacing: float = 1.0,
                 spawns: list[tuple[float, str]] | None = None, rebuild: bool = False,
                 b_damage: float = 1.0, replace_rate: float | None = None, base_spacing: float = 0.0):
        self.cal = cal or Calibration()
        # Side B's damage multiplier: what the AI gains from fighting at home (focus fire on
        # whatever enters its base, units joining from the factories next door), fitted to
        # recorded battles.
        self.b_damage = b_damage
        # Credits per frame the AI spends remaking lost units (None: no limit, 0: units are not
        # remade); buildings are rebuilt regardless.
        self.replace_rate = replace_rate
        spawns = sorted(spawns or [])
        ids_a = [tid for tid, n in army_a.items() for _ in range(n)]
        ids_b = [tid for tid, n in army_b.items() for _ in range(n)] + [t for _, t in spawns]
        self.ids = ids_a + ids_b
        technos = [db.technos[t] for t in self.ids]
        n = len(technos)
        self.side = np.array([0] * len(ids_a) + [1] * len(ids_b))
        self.spawn_at = np.array([0.0] * (n - len(spawns)) + [f for f, _ in spawns])
        self.building = np.array([t.kind == "building" for t in technos], bool)
        # Which factory category each unit needs standing to appear, and which each building is.
        self.needs = [t.category for t in technos]
        self.makes = [t.factory if t.kind == "building" else None for t in technos]
        self.rebuild = rebuild
        self.rebuild_frames = np.array([db.build_frames(t) for t in technos], float)
        # The factory kind that remakes each loss: the construction yard for buildings.
        self.remade_by = ["building" if t.kind == "building" else t.category for t in technos]
        self.cost = np.array([t.cost for t in technos], float)
        self.max_hp = np.array([t.strength for t in technos], float)
        self.air = np.array([t.is_air for t in technos], bool)
        # Kirov-like bombers (air, bombs reach <= 2 cells) only hit buildings while any stand: live
        # 2026-11-06 they never got over a moving unit (assault.Assault.bomber does the same).
        self.bomber = self.air & np.array([bool(t.combat_weapons) and max(w.range for w in t.combat_weapons) <= 2
                                           for t in technos], bool)
        self.speed = np.array([t.speed for t in technos], float) * self.cal.speed_to_cells
        self.ammo_max = np.array([t.ammo for t in technos])
        self.infantry = np.array([t.kind == "infantry" for t in technos], bool)
        self.crusher = np.array([t.crusher and t.kind == "vehicle" for t in technos], bool)
        armor = np.array([t.armor for t in technos], int)

        dmg = np.zeros((n, 2))
        rof = np.ones((n, 2))
        rng = np.zeros((n, 2))
        minr = np.zeros((n, 2))
        spread = np.zeros((n, 2))
        pam = np.ones((n, 2))
        suicide = np.zeros((n, 2), bool)
        verses = np.zeros((n, 2, len(ARMOR_TYPES)))
        can_aa = np.zeros((n, 2), bool)
        can_ag = np.zeros((n, 2), bool)
        deployer = np.zeros(n, bool)
        for i, t in enumerate(technos):
            weapons = t.combat_weapons[:2]
            # Deploy-to-fire units keep their secondary in slot 1, usable only while deployed.
            deployer[i] = t.deploy_fire and len(weapons) == 2
            for s, w in enumerate(weapons):
                dmg[i, s] = w.damage * w.burst
                rof[i, s] = w.rof
                rng[i, s] = w.range
                minr[i, s] = w.min_range
                spread[i, s] = w.cell_spread
                pam[i, s] = w.percent_at_max
                suicide[i, s] = w.suicide
                verses[i, s] = w.verses
                can_aa[i, s] = w.aa
                can_ag[i, s] = w.ag
        # Prism Towers chain: the others beam into the one firing, each adding PrismSupportModifier
        # (150%, rulesmd [General]) of its damage. N towers then fire one beam of 1 + 1.5 (N - 1)
        # instead of N beams: live 2026-11-06 (game 14) eight one-shot our flak tracks.
        prism = np.array([any(w.id == "PrismSupport" for w in t.combat_weapons) for t in technos], bool)
        for side in (0, 1):
            towers = prism & (self.side == side)
            k = min(int(towers.sum()), 9)
            if k > 1:
                dmg[towers, 0] *= (1 + 1.5 * (k - 1)) / k
        self.dmg, self.rof, self.rng, self.minr = dmg, rof, rng, minr
        self.spread, self.pam, self.suicide, self.verses = spread, pam, suicide, verses
        self.armor = armor
        self.deployer = deployer
        # Siege Chopper style units land when deployed and become ground targets.
        self.lands = np.array([t.is_air for t in technos], bool) & deployer

        # base[i, j, s]: damage per shot of unit i's slot s against unit j (enemies only).
        base = dmg[:, None, :] * verses[:, :, armor].transpose(0, 2, 1)
        enemy = self.side[:, None] != self.side[None, :]
        base = np.where(enemy[:, :, None], base, 0.0)
        self.eff_vs_ground = base * can_ag[:, None, :]
        self.eff_vs_air = base * can_aa[:, None, :]
        self.slots_mobile = np.where(deployer[:, None], [True, False], [True, True])
        self.slots_deployed = np.where(deployer[:, None], [False, True], [True, True])

        self.home = np.concatenate([
            _formation(len(ids_a), 0.0, -1.0, spacing),
            _formation(len(ids_b), distance, 1.0, spacing),
        ]) if n else np.zeros((0, 2))
        if base_spacing and n:
            # Buildings spread over a base behind their side's units, not packed with them: with
            # every AI building 1.5 cells apart the Kirovs' splash razed whole bases that live
            # 2026-11-06 they barely scratched. 4 cells matched the battles replayed at the
            # buildings' real positions (games 4-12 of series_v4_20261106).
            for side, x0, sign in ((0, 0.0, -1.0), (1, distance, 1.0)):
                bi = np.nonzero((self.side == side) & self.building)[0]
                if len(bi):
                    self.home[bi] = _formation(len(bi), x0 + sign * 2.0, sign, base_spacing)

    def _tables(self, deployed: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Best weapon slot and damage for every (attacker, target) pair given deploy states."""
        air_now = self.air & ~(self.lands & deployed)
        eff = np.where(air_now[None, :, None], self.eff_vs_air, self.eff_vs_ground)
        slots = np.where(deployed[:, None], self.slots_deployed, self.slots_mobile)
        eff = eff * slots[:, None, :]
        return eff.argmax(axis=2), eff.max(axis=2), air_now

    def run(self, dt: int = 5, max_frames: int = 5400, crush: bool = True,
            kite: bool = False, focus: bool = False) -> CombatResult:
        """`focus`: side A picks targets like the live controller (assault.py) instead of the
        nearest: the most enemy cost destroyed per frame, i.e. cost * damage rate / health left,
        so hurt targets draw the fire of everyone that can hurt them (focus fire) and each unit
        goes for what its weapon counters (anti-air on aircraft, anti-armour on tanks); armed
        enemies count 4x. Only targets already in range are weighed: walking past the enemy in
        front to reach a better one lost every test battle. Nothing in range: the nearest."""
        n = len(self.ids)
        if n == 0 or not (self.side == 0).any() or not (self.side == 1).any():
            return self._result(0, np.ones(n, bool), self.max_hp.copy())
        pos = self.home.copy()
        hp = self.max_hp.copy()
        pending = self.spawn_at > 0
        alive = ~pending
        appeared = alive.copy()  # every unit that took part, for the value shares
        ruins: dict[str, list[int]] = {}  # factory kind -> B losses waiting to be remade, oldest first
        rebuilding: dict[str, tuple[int, float]] = {}  # factory kind -> (loss, ready frame)
        replaced = 0.0  # credits spent remaking units
        cool = np.zeros(n)
        ammo = self.ammo_max.copy()
        away = np.zeros(n)  # frames until a rearming aircraft returns
        deployed = np.zeros(n, bool)
        best_slot, best_eff, air_now = self._tables(deployed)
        dep_ground = self.eff_vs_ground[:, :, 1]
        dep_air = self.eff_vs_air[:, :, 1]
        ar = np.arange(n)
        frame = 0
        while frame < max_frames:
            if pending.any() or any(ruins.values()) or rebuilding:
                b_makes = {self.makes[i] for i in np.nonzero(alive & self.building & (self.side == 1))[0]}
                for i in np.nonzero(pending & (self.spawn_at <= frame))[0]:
                    pending[i] = False
                    if self.needs[i] in b_makes:  # no factory left: the AI cannot make it
                        alive[i] = appeared[i] = True
                for kind, queue in ruins.items():
                    if kind not in rebuilding and queue and kind in b_makes:
                        i = queue[0]
                        if kind != "building" and self.replace_rate is not None                                 and replaced + self.cost[i] > self.replace_rate * frame:
                            continue  # the AI cannot afford it yet
                        queue.pop(0)
                        if kind != "building":
                            replaced += self.cost[i]
                        rebuilding[kind] = (i, frame + self.rebuild_frames[i])
                for kind, (i, ready) in list(rebuilding.items()):
                    if kind not in b_makes:  # its factory fell: the work is lost
                        del rebuilding[kind]
                    elif frame >= ready:
                        alive[i], hp[i], cool[i] = True, self.max_hp[i], 0.0
                        pos[i] = self.home[i]  # out of the factory, at the back of the base
                        del rebuilding[kind]
            before = alive.copy()
            present = alive & (away <= 0)
            diff = pos[:, None, :] - pos[None, :, :]
            dist = np.sqrt((diff ** 2).sum(axis=2))

            # Deploy when an enemy enters deploy-weapon range; undeploy when none is left there.
            if self.deployer.any():
                dep_eff = np.where(air_now[None, :], dep_air, dep_ground)
                reach = (dep_eff > 0) & present[None, :] & (dist <= self.rng[:, 1:2])
                want = self.deployer & present & reach.any(axis=1)
                # Units that land to deploy only do so when no ground-capable enemy is close.
                if self.lands.any():
                    ground_threat = (self.eff_vs_ground.max(axis=2).T > 0) & present[None, :] &                         (dist <= self.rng.max(axis=1)[None, :] + 2.0)
                    want &= ~(self.lands & ground_threat.any(axis=1))
                if (want != (deployed & self.deployer)).any():
                    deployed = want
                    best_slot, best_eff, air_now = self._tables(deployed)

            valid = (best_eff > 0) & present[:, None] & present[None, :]
            if self.bomber.any():
                for side in (0, 1):
                    standing = self.building & present & (self.side != side)
                    if standing.any():
                        valid[np.ix_(self.bomber & (self.side == side), ~standing)] = False
            masked = np.where(valid, dist, np.inf)
            tgt = masked.argmin(axis=1)
            if focus:
                ours = np.nonzero(present & (self.side == 0))[0]
                if len(ours):
                    slots = best_slot[ours]
                    reach = valid[ours] & (dist[ours] <= self.rng[ours[:, None], slots])                         & (dist[ours] >= self.minr[ours[:, None], slots])
                    rate = best_eff[ours] / self.rof[ours[:, None], slots]
                    armed = (best_eff[:, self.side == 0] > 0).any(axis=1)
                    score = self.cost[None, :] * rate / np.maximum(hp, 1.0)[None, :] * np.where(armed, 4.0, 1.0)[None, :]
                    pick = np.where(reach, score, -1.0).argmax(axis=1)
                    shoot = reach.any(axis=1)  # something in range: the best of it; else walk to the nearest
                    tgt[ours[shoot]] = pick[shoot]
            tdist = masked[ar, tgt]
            has = np.isfinite(tdist)
            if not has.any() and not (alive & (away > 0)).any():
                break
            slot = best_slot[ar, tgt]
            rng = self.rng[ar, slot]
            in_range = has & (tdist <= rng) & (tdist >= self.minr[ar, slot])
            fire = in_range & (cool <= 0)
            mobile = ~deployed

            # Movement toward targets that are out of range; crushers drive into infantry.
            crushing = (has & self.crusher & self.infantry[tgt] & mobile) if crush else np.zeros(n, bool)
            move = has & mobile & (~in_range | crushing)
            if move.any():
                mi = np.nonzero(move)[0]
                vec = pos[tgt[mi]] - pos[mi]
                d = tdist[mi]
                stop = np.where(crushing[mi], 0.0, rng[mi] * 0.95)
                step = np.minimum(self.speed[mi] * dt, np.maximum(d - stop, 0))
                pos[mi] += vec / np.maximum(d, 1e-9)[:, None] * step[:, None]
            if crushing.any():
                ci = np.nonzero(crushing)[0]
                gap = np.sqrt(((pos[ci][:, None, :] - pos[None, :, :]) ** 2).sum(axis=2))
                victims = (gap < 0.5) & self.infantry[None, :] & present[None, :] & \
                    (self.side[None, :] != self.side[ci][:, None])
                hp[victims.any(axis=0)] = 0

            # Kiting: while reloading, a faster unit that outranges its target backs off.
            if kite:
                threat = np.where(best_eff[tgt, ar] > 0, self.rng[tgt, best_slot[tgt, ar]], 0.0)
                back = in_range & mobile & ~fire & (self.speed > self.speed[tgt]) & (rng > threat) & \
                    (tdist < rng * 0.95)
                if back.any():
                    bi = np.nonzero(back)[0]
                    vec = pos[bi] - pos[tgt[bi]]
                    step = np.minimum(self.speed[bi] * dt, rng[bi] * 0.95 - tdist[bi])
                    pos[bi] += vec / np.maximum(tdist[bi], 1e-9)[:, None] * step[:, None]

            # Damage, including splash on same-layer units of the target's side.
            if fire.any():
                fi = np.nonzero(fire)[0]
                fs = slot[fi]
                ft = tgt[fi]
                shots = np.maximum(1.0, dt / self.rof[fi, fs])
                spread = self.spread[fi, fs]
                d2 = dist[ft]  # (F, N) distance from each impact point to every unit
                same = (self.side[None, :] == self.side[ft][:, None]) & \
                    (air_now[None, :] == air_now[ft][:, None]) & present[None, :]
                hit = same & ((d2 <= spread[:, None]) | (ar[None, :] == ft[:, None]))
                falloff = 1 - (1 - self.pam[fi, fs])[:, None] * d2 / np.maximum(spread, 1e-9)[:, None]
                falloff = np.where(ar[None, :] == ft[:, None], 1.0, falloff)
                per_target = self.dmg[fi, fs][:, None] * self.verses[fi, fs][:, self.armor]
                if self.b_damage != 1.0:
                    per_target = per_target * np.where(self.side[fi] == 1, self.b_damage, 1.0)[:, None]
                hp -= (per_target * falloff * hit * shots[:, None]).sum(axis=0)
                cool[fi] = self.rof[fi, fs]
                alive[fi[self.suicide[fi, fs]]] = False
                limited = fi[ammo[fi] > 0]
                ammo[limited] -= 1
                out = limited[ammo[limited] == 0]
                away[out] = self.cal.aircraft_reload_frames
                pos[out] = self.home[out]

            alive &= hp > 0
            if self.rebuild:
                for i in np.nonzero(before & ~alive & (self.side == 1))[0]:
                    if self.makes[i] != "building" and (self.building[i] or self.replace_rate != 0):
                        ruins.setdefault(self.remade_by[i], []).append(i)  # a lost CY stays lost
            cool -= dt
            returning = (away > 0) & (away - dt <= 0)
            ammo[returning] = self.ammo_max[returning]
            away -= dt
            frame += dt
            if not (alive & (self.side == 0)).any() or not (alive & (self.side == 1)).any():
                break
        return self._result(frame, alive, hp, appeared)

    def _result(self, frame: int, alive: np.ndarray, hp: np.ndarray,
                appeared: np.ndarray | None = None) -> CombatResult:
        appeared = np.ones(len(self.ids), bool) if appeared is None else appeared
        left = np.where(alive, np.clip(hp, 0, None) / np.maximum(self.max_hp, 1), 0.0) * self.cost
        totals = [self.cost[(self.side == s) & appeared].sum() or 1.0 for s in (0, 1)]
        a_left = left[self.side == 0].sum() / totals[0]
        b_left = left[self.side == 1].sum() / totals[1]
        a_alive = (alive & (self.side == 0)).any()
        b_alive = (alive & (self.side == 1)).any()
        winner = "A" if a_alive and not b_alive else "B" if b_alive and not a_alive else "draw"
        surv = [{}, {}]
        for i in np.nonzero(alive)[0]:
            surv[self.side[i]][self.ids[i]] = surv[self.side[i]].get(self.ids[i], 0) + 1
        return CombatResult(frame, winner, float(a_left), float(b_left), surv[0], surv[1])


def simulate(db: GameData, army_a: dict[str, int], army_b: dict[str, int],
             cal: Calibration | None = None, distance: float = 12.0, dt: int = 5,
             max_frames: int = 5400, crush: bool = True, kite: bool = False,
             spacing: float = 1.0) -> CombatResult:
    return Battle(db, army_a, army_b, cal, distance, spacing).run(dt, max_frames, crush, kite)
