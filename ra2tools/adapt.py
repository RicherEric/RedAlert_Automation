"""React to what the AI actually fields, read from memory during a live game.

The offline plan (robust search) is fixed before the game. Live we see every AI unit (memory
reads the whole map: fine against the computer, never to be used online), so:

- counter_pick: battle-simulate our planned army against the AI's current units; while it loses,
  add the candidate unit that gains the most score per credit (anti-air against aircraft,
  anti-armour against tanks come out of the damage tables, not a hand-written list).
- Reinforcer: once the army has left, spend surplus credits on that counter and keep those units
  home, where the hard AI's waves razed the empty base before (live 2026-11-03).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .bridge import BridgeError
from .combat import Battle
from .gamedata import GameData
from .memory import Snapshot

NOT_COMBAT = {"AMCV", "SMCV", "PCV"}


def enemy_army(db: GameData, snap: Snapshot, enemy: int) -> dict[str, int]:
    """The AI's combat units now (no buildings, harvesters or MCVs)."""
    out = Counter()
    for o in snap.objects:
        t = db.technos.get(o.type_id)
        if (o.owner == enemy and o.on_map and o.health > 0 and o.kind != "building" and t is not None
                and t.combat_weapons and not t.harvester and o.type_id not in NOT_COMBAT):
            out[o.type_id] += 1
    return dict(out)


def counter_pick(db: GameData, cal, combat, ours: dict[str, int], enemy: dict[str, int],
                 candidates: list[str], budget: float, step: int = 2, good: float = 0.3,
                 rounds: int = 3) -> dict[str, int]:
    """Greedy additions to `ours` (at most `budget` credits, `rounds` picks) until it beats
    `enemy` by `good`. Each round is one battle per candidate (~30 ms each live)."""
    def score(army):
        return Battle(db, army, enemy, cal, combat.distance, combat.spacing, base_spacing=combat.base_spacing).run(
            combat.dt, combat.max_frames, combat.crush, combat.kite, combat.focus).score
    if not enemy:
        return {}
    army, added, spent = Counter(ours), Counter(), 0.0
    now = score(army)
    for _ in range(rounds):
        if now >= good:
            break
        best = None
        for c in candidates:
            cost = db.technos[c].cost * step
            if spent + cost > budget:
                continue
            gain = (score(army + Counter({c: step})) - now) / cost
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, c, cost)
        if best is None:
            break
        _, c, cost = best
        army[c] += step
        added[c] += step
        spent += cost
        now = score(army)
    return dict(added)


@dataclass
class Reinforcer:
    """After the build: surplus credits buy counters to the AI's current army, kept home."""
    db: GameData
    commander: object
    cal: object
    combat: object
    enemy: int
    candidates: list[str]
    reserve: int = 1500           # credits left for repairs
    every: int = 600              # frames between decisions
    home: Counter = field(default_factory=Counter)  # what we planned to keep home
    units: set = field(default_factory=set)         # addresses of the units it made
    log: list = field(default_factory=list)
    _last: int = -10**9
    _before: set = field(default_factory=set)
    _queued: dict = field(default_factory=dict)     # category -> type

    def category(self, tid: str) -> str:
        t = self.db.technos[tid]
        return t.kind if t.kind in ("infantry", "aircraft") else "vehicle"

    def tick(self, snap: Snapshot) -> None:
        mine = {o.address: o for o in snap.objects if o.owner == snap.player_index and o.on_map and o.health > 0}
        new = set(mine) - self._before
        if self._before:
            for a in new:  # a unit of a type we queued has come out: it is ours to keep home
                if mine[a].type_id in self._queued.values() and mine[a].kind != "building":
                    self.units.add(a)
        self._before = set(mine)
        busy = {f.category for f in snap.factories}
        self._queued = {c: t for c, t in self._queued.items() if c in busy}
        credits = snap.player.credits if snap.player else 0
        if snap.frame - self._last < self.every or credits < self.reserve + 500:
            return
        self._last = snap.frame
        enemy = enemy_army(self.db, snap, self.enemy)
        here = Counter(mine[a].type_id for a in self.units if a in mine)
        pick = counter_pick(self.db, self.cal, self.combat, dict(here + self.home), enemy,
                            self.candidates, credits - self.reserve, step=1)
        for tid in pick:
            cat = self.category(tid)
            if cat in busy or cat in self._queued:
                continue
            try:
                self.commander.produce(tid)
            except BridgeError:
                continue
            self._queued[cat] = tid
            self.log.append((snap.frame, "reinforce", tid))
