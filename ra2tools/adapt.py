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
    # A drop the units kept home must beat, bought for first (the AI's chronoshifted tanks).
    home_threat: dict = field(default_factory=dict)
    # AI units at our base right now, and what of ours is there to meet them: bought against
    # first, every `raid_every` frames. Live 2026-11-11 (series_v13 game 3) a raid razed the base
    # in 6000 frames while $11-13k sat in the bank and units came one every 600+ frames.
    raid: dict = field(default_factory=dict)
    defenders: dict = field(default_factory=dict)
    raid_every: int = 60
    units: set = field(default_factory=set)         # addresses of the units it made
    log: list = field(default_factory=list)
    _last: int = -10**9
    _before: set = field(default_factory=set)
    _queued: dict = field(default_factory=dict)     # category -> type
    _ticked: int | None = None                      # frame of the last tick
    # It only ticks while a wave is out. Live 2026-11-10 (series_v12 game 2) it resumed after a
    # 9000-frame regroup and took the whole rebuilt wave for its own (their types matched a stale
    # queue entry), kept it home and made the wave look lost 22 frames after it gathered.
    MAX_GAP = 120

    def category(self, tid: str) -> str:
        t = self.db.technos[tid]
        return t.kind if t.kind in ("infantry", "aircraft") else "vehicle"

    def tick(self, snap: Snapshot) -> None:
        mine = {o.address: o for o in snap.objects if o.owner == snap.player_index and o.on_map and o.health > 0}
        new = set(mine) - self._before
        gap = self._ticked is not None and snap.frame - self._ticked > self.MAX_GAP
        self._ticked = snap.frame
        if gap:  # what came out meanwhile was not ours, and the queue we knew of is gone
            self._queued.clear()
        elif self._before:
            for a in new:  # a unit of a type we queued has come out: it is ours to keep home
                if mine[a].type_id in self._queued.values() and mine[a].kind != "building":
                    self.units.add(a)
        self._before = set(mine)
        busy = {f.category for f in snap.factories}
        self._queued = {c: t for c, t in self._queued.items() if c in busy}
        credits = snap.player.credits if snap.player else 0
        budget = credits - self.reserve
        free = {self.category(t) for t in self.candidates} - busy - set(self._queued)
        pace = self.raid_every if self.raid and free else self.every
        if credits < self.reserve + 500 or snap.frame - self._last < pace:
            return
        # Against a raid the defenders at home must win; only then the usual picks, at their pace.
        pick = counter_pick(self.db, self.cal, self.combat, self.defenders, self.raid,
                            [t for t in self.candidates if self.category(t) in free], budget,
                            step=1) if self.raid and free else {}
        if not pick and snap.frame - self._last < self.every:
            return
        self._last = snap.frame
        enemy = enemy_army(self.db, snap, self.enemy)
        here = Counter(mine[a].type_id for a in self.units if a in mine)
        pick = pick or (counter_pick(self.db, self.cal, self.combat, dict(here + self.home), self.home_threat,
                                     self.candidates, budget, step=1) if self.home_threat else {})
        pick = pick or counter_pick(self.db, self.cal, self.combat, dict(here + self.home), enemy,
                                    self.candidates, budget, step=1)
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


@dataclass
class Rebuilder:
    """After a wave is lost: spend the bank on the plan's units again, for the next wave.

    Live 2026-11-07 (series_v7 games 1-3) the first wave died at the AI base, nothing else was
    built and $30-40k piled up until the timeout while the AI grew to 50 buildings."""
    db: GameData
    commander: object
    plan: dict                    # unit type -> count in the attack plan (its mix is rebuilt)
    reserve: int = 1500           # credits left for repairs
    log: list = field(default_factory=list)
    # More war factories while the bank is full (each one cuts every unit's build time to 0.8x,
    # rules MultipleFactory): live 2026-11-09 (series_v10 game 5) $22k sat unspent through every
    # regroup while 1-2 factories made 14-18 units a wave.
    factory: str | None = None    # our war factory type (NAWEAP); None: never add one
    max_factories: int = 4
    placer: object = None         # BuildExecutor.placer (placement.Spreader)
    _adding: object = None        # the BuildExecutor placing the new factory

    def __post_init__(self):
        # BuildLimit units (Boris) are not queued again: the game refuses them while one lives.
        self.plan = {t: n for t, n in self.plan.items() if n > 0 and not self.db.technos[t].build_limit
                     and self.db.technos[t].kind != "building" and self.db.technos[t].combat_weapons}

    def category(self, tid: str) -> str:
        t = self.db.technos[tid]
        return t.kind if t.kind in ("infantry", "aircraft") else "vehicle"

    def broke(self, snap: Snapshot) -> bool:
        """Nothing more to buy: too few credits for any plan unit and no factory still at it."""
        credits = snap.player.credits if snap.player else 0
        busy = {f.category for f in snap.factories}
        return (not any(self.category(t) in busy for t in self.plan)
                and all(self.db.technos[t].cost > credits - self.reserve for t in self.plan))

    def tick(self, snap: Snapshot, have: Counter) -> None:
        """Queue, in every idle factory, the plan unit furthest below its share of `have`;
        with twice a war factory's price to spare, another war factory."""
        credits = (snap.player.credits if snap.player else 0) - self.reserve
        busy = {f.category for f in snap.factories}
        self._add_factory(snap, credits, busy)
        for cat in sorted({self.category(t) for t in self.plan} - busy):
            # Up to the plan's count of each: live 2026-11-09 (series_v10 game 4) the barracks kept
            # making the plan's only infantry and wave 2 left with 83 conscripts and 4 Apocalypses.
            options = [t for t in self.plan if self.category(t) == cat and self.db.technos[t].cost <= credits
                       and have.get(t, 0) < self.plan[t]]
            self._queue(snap, options, have)

    def _add_factory(self, snap: Snapshot, credits: int, busy: set) -> None:
        from .executor import BuildExecutor
        if self._adding is not None:
            try:
                self._adding.tick(snap)
            except BridgeError:
                pass
            if self._adding.done:
                self._adding = None
            return
        if self.factory is None or "building" in busy or credits < 2 * self.db.technos[self.factory].cost:
            return
        mine = sum(1 for o in snap.objects if o.owner == snap.player_index and o.type_id == self.factory
                   and o.on_map and o.health > 0)
        if not 0 < mine < self.max_factories:
            return  # none left means no yard or tech either: the Steward rebuilds the first
        self._adding = BuildExecutor(self.db, [self.factory], self.commander, placer=self.placer)
        self._adding.deploy_frame = snap.frame
        self.log.append((snap.frame, "factory", self.factory))
        try:
            self._adding.tick(snap)
        except BridgeError:
            self._adding = None

    def _queue(self, snap: Snapshot, options: list[str], have: Counter) -> bool:
        # Most needed first, but anything the factory can make: live 2026-11-09 (series_v10
        # game 2) the tech centre was lost, only Apocalypses were tried and $43k sat unspent.
        for tid in sorted(options, key=lambda t: have.get(t, 0) / self.plan[t]):
            try:
                self.commander.produce(tid)
            except BridgeError:
                continue
            self.log.append((snap.frame, "rebuild", tid))
            return True
        return False
