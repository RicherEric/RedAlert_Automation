"""Base upkeep in a live game, next to the build and the army controllers (execute.py).

Repairs damaged buildings, sends engineers to capture tech oil derricks (then the tech airport,
whose paratroopers otherwise go to the AI), puts idle harvesters back to work on the best ore
field (gems first, `fields` from mapdata), and garrisons spare infantry in the civilian building
nearest our base. All from the memory snapshot, every order through the bridge.

Live vs hard 2026-11-06: damaged buildings were never repaired; the AI took a derrick at 18805
while ours 13 cells away stayed neutral; after a lightning storm took the refinery, harvesters
sat still (3% of samples moving).

Once the build is done (`rebuild` set by execute.py; before that the build policy owns the
production queues) it also rebuilds lost power plants, refineries and factories: the AI's
lightning storm took the refinery and war factory three times in one game and nothing rebuilt them.

Units this controller claims (engineers, garrison infantry) are in `claimed`, so the Defence and
Assault controllers can leave them alone (Assault.skip).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from collections import Counter

from .bridge import BridgeError
from .executor import BuildExecutor
from .gamedata import GameData
from .memory import ObjectState, Snapshot

AIRPORTS = {"CAAIRP"}
# Repair the buildings that keep the plan going first; at most `max_repairs` at once, since
# repairs eat the money production needs.
REPAIR_FIRST = ("cnst", "factory", "proc", "power", "barracks", "radar", "tech")
REBUILD = ("power", "proc", "factory", "barracks", "radar", "tech")  # in this order
REBUILD_START = 300  # frames a rebuild may wait to start (prerequisite lost too) before trying another


@dataclass
class Steward:
    db: GameData
    commander: object
    country: str
    fields: list = field(default_factory=list)  # mapdata.Field
    repair_below: float = 0.85      # share of Strength
    repair_reserve: int = 400       # credits kept for production
    max_repairs: int = 2
    capture_range: float = 80.0     # cells from our base
    harvest_idle: int = 450         # frames without moving before a harvester is sent back
    garrison: int = 5               # infantry to put in the nearest civilian building
    garrison_types: frozenset = frozenset({"E2", "E1", "INIT"})  # rules Occupier=yes
    garrison_range: float = 30.0    # cells from our base
    rebuild: bool = False
    placer: object = None           # BuildExecutor.placer for the rebuilt buildings (placement.Spreader)
    claimed: set = field(default_factory=set)
    log: list = field(default_factory=list)  # (frame, what, detail) for the run log
    _repairing: dict = field(default_factory=dict)   # building -> (frame sent, health then)
    _capture: dict = field(default_factory=dict)     # engineer -> [target, frame, attempts]
    _harvest: dict = field(default_factory=dict)     # harvester -> [x, y, frame last moved, frame ordered]
    _sent_to: dict = field(default_factory=dict)     # harvester -> field index it was sent to
    _dry: set = field(default_factory=set)           # field indices a harvester went idle on
    _garrison: dict = field(default_factory=dict)    # infantry -> [building, frame, attempts, type]
    inside: Counter = field(default_factory=Counter)  # garrisoned types now off the map (updated in place)
    _peak: Counter = field(default_factory=Counter)  # most of each key building we had
    _rebuilding: BuildExecutor | None = None
    _blocked: dict = field(default_factory=dict)     # building -> frame its rebuild could not start

    # ------------------------------------------------------------------ helpers
    def _mine(self, snap: Snapshot) -> list[ObjectState]:
        return [o for o in snap.objects if o.owner == snap.player_index and o.on_map and o.health > 0]

    def _home(self, mine: list[ObjectState]) -> tuple[float, float] | None:
        b = [(o.x, o.y) for o in mine if o.kind == "building"]
        return (sum(x for x, _ in b) / len(b), sum(y for _, y in b) / len(b)) if b else None

    def _neutral(self, snap: Snapshot) -> set[int]:
        return {h.index for h in snap.houses if h.country in ("Neutral", "Special")}

    def _order(self, what: str, call, *args) -> bool:
        try:
            call(*args)
            return True
        except BridgeError as e:
            self.log.append((None, what + " failed", str(e)))
            return False

    def tick(self, snap: Snapshot) -> None:
        mine = self._mine(snap)
        home = self._home(mine)
        if home is None:
            return
        enemies = [o for o in snap.objects if o.owner != snap.player_index and o.on_map and o.health > 0
                   and o.owner not in self._neutral(snap)]
        self._repair(snap, mine, enemies)
        self._engineers(snap, mine, home)
        self._harvesters(snap, mine)
        self._occupy(snap, mine, home)
        self._rebuild(snap, mine)
        self.claimed &= {o.address for o in mine} | set(self._garrison)  # garrisoned units leave the map

    # ------------------------------------------------------------------ repair
    def _repair(self, snap: Snapshot, mine: list[ObjectState], enemies: list[ObjectState]) -> None:
        credits = snap.player.credits if snap.player else 0
        buildings = {o.address: o for o in mine if o.kind == "building"}
        for addr in list(self._repairing):
            o = buildings.get(addr)
            if o is None or o.health >= self.db.technos[o.type_id].strength:
                del self._repairing[addr]  # gone, or full: the game stops repairing by itself
                continue
            sent, health = self._repairing[addr]
            quiet = not any(math.dist((o.x, o.y), (e.x, e.y)) <= 10 for e in enemies)
            if snap.frame - sent >= 450 and o.health <= health and quiet:
                # No progress and nobody shooting it: the toggle did not take (or ran out of
                # money and stopped); toggle again.
                if self._order("repair", self.commander.repair, addr):
                    self._repairing[addr] = (snap.frame, o.health)
        if credits < self.repair_reserve or len(self._repairing) >= self.max_repairs:
            return
        basics = self.db.basics(self.country)
        rank = {basics[k]: i for i, k in enumerate(REPAIR_FIRST) if k in basics}
        hurt = [o for o in buildings.values() if o.address not in self._repairing
                and o.health < self.repair_below * self.db.technos[o.type_id].strength]
        for o in sorted(hurt, key=lambda o: (rank.get(o.type_id, 99), o.health)):
            if len(self._repairing) >= self.max_repairs:
                break
            if self._order("repair", self.commander.repair, o.address):
                self._repairing[o.address] = (snap.frame, o.health)
                self.log.append((snap.frame, "repair", o.type_id))

    # ---------------------------------------------------------------- engineers
    def _capturable(self, snap: Snapshot, home) -> list[ObjectState]:
        """Derricks first, then airports, nearest our base first; neutral ones, or the AI's
        within half the capture range."""
        neutral = self._neutral(snap)
        out = []
        for o in snap.objects:
            t = self.db.technos.get(o.type_id)
            if (o.kind != "building" or not o.on_map or o.health <= 0 or o.owner == snap.player_index
                    or t is None or not t.capturable or not (t.cash_amount or o.type_id in AIRPORTS)):
                continue
            d = math.dist((o.x, o.y), home)
            if d <= (self.capture_range if o.owner in neutral else self.capture_range / 2):
                out.append((0 if t.cash_amount else 1, d, o))
        return [o for *_, o in sorted(out, key=lambda x: x[:2])]

    def _engineers(self, snap: Snapshot, mine: list[ObjectState], home) -> None:
        engineers = [o for o in mine if self.db.technos.get(o.type_id) and self.db.technos[o.type_id].engineer]
        if not engineers:
            return
        targets = self._capturable(snap, home)
        alive = {o.address for o in targets}
        taken = {v[0] for a, v in self._capture.items() if v[0] in alive}
        for e in engineers:
            self.claimed.add(e.address)
            job = self._capture.get(e.address)
            if job and job[0] in alive and snap.frame - job[1] < 900:
                continue
            if job and job[0] in alive:  # still not there: re-send, trying where the target goes
                target = job[0]
                job[2] += 1
            else:
                free = [o for o in targets if o.address not in taken]
                if not free:
                    continue
                target = min(free[:2], key=lambda o: math.dist((o.x, o.y), (e.x, e.y))).address
                job = self._capture[e.address] = [target, snap.frame, 0]
                taken.add(target)
            where = ("d", "td", "t")[job[2] % 3]
            if self._order("capture", self.commander.capture, e.address, target, where):
                job[1] = snap.frame
                self.log.append((snap.frame, "capture", f"0x{target:08x} {where}"))

    # --------------------------------------------------------------- harvesters
    def _field_for(self, h: ObjectState) -> int | None:
        """Best field for an idle harvester: credits per bale over the distance, gems first."""
        best, score = None, 0.0
        for i, f in enumerate(self.fields):
            if i in self._dry:
                continue
            s = f.value / (5.0 + math.dist((h.x, h.y), f.centre))
            if s > score:
                best, score = i, s
        return best

    def _harvesters(self, snap: Snapshot, mine: list[ObjectState]) -> None:
        refineries = [o for o in mine if o.kind == "building" and self.db.technos.get(o.type_id)
                      and self.db.technos[o.type_id].storage and self.db.technos[o.type_id].free_unit]
        if not refineries:
            return  # nowhere to bring ore: the build will make a refinery
        for h in mine:
            t = self.db.technos.get(h.type_id)
            if t is None or not t.harvester or h.kind == "building":
                continue
            st = self._harvest.get(h.address)
            if st is None or math.dist((st[0], st[1]), (h.x, h.y)) > 0.3:
                self._harvest[h.address] = [h.x, h.y, snap.frame, st[3] if st else -10**9]
                continue
            if snap.frame - st[2] < self.harvest_idle or snap.frame - st[3] < 900:
                continue
            if min(math.dist((h.x, h.y), (r.x, r.y)) for r in refineries) <= 4:
                continue  # waiting at the dock
            if h.address in self._sent_to:  # went idle where we sent it: that field is mined out
                self._dry.add(self._sent_to.pop(h.address))
            i = self._field_for(h)
            cell = None
            if i is not None:
                f = self.fields[i]
                x, y, _ = max(f.cells, key=lambda c: (c[2], -math.dist(c[:2], (h.x, h.y))))
                cell = (x, y)
                self._sent_to[h.address] = i
            if self._order("harvest", self.commander.harvest, h.address, cell):
                st[3] = snap.frame
                self.log.append((snap.frame, "harvest", f"{h.type_id} -> {cell or 'nearest'}"))

    # ------------------------------------------------------------------ garrison
    def _occupy(self, snap: Snapshot, mine: list[ObjectState], home) -> None:
        if self.garrison <= 0:
            return
        houses = self._neutral(snap) | {snap.player_index}
        spots = []
        for o in snap.objects:
            if o.kind != "building" or not o.on_map or o.health <= 0 or o.owner not in houses:
                continue
            t = self.db.technos.get(o.type_id)
            if t is None or not t.occupants:
                continue
            d = math.dist((o.x, o.y), home)
            if d <= self.garrison_range:
                spots.append((d, o))
        if not spots:
            return
        building = min(spots, key=lambda p: p[0])[1]
        # Still on the map = still walking (inside a building a unit is off the map). Garrisoning is
        # the Capture mission: given Enter, conscripts shot the building down (live 2026-11-06).
        walking = {o.address for o in mine} & set(self._garrison)
        self.inside.clear()
        self.inside.update(job[3] for a, job in self._garrison.items() if a not in walking)
        for a in walking:
            job = self._garrison[a]
            if snap.frame - job[1] >= 900 and job[2] < 3:  # not in yet: again
                job[2] += 1
                job[1] = snap.frame
                self._order("garrison", self.commander.capture, a, job[0], ("d", "td", "t")[job[2] % 3])
        if len(self._garrison) >= self.garrison:
            return
        free = sorted((o for o in mine if o.type_id in self.garrison_types and o.address not in self.claimed),
                      key=lambda o: math.dist((o.x, o.y), (building.x, building.y)))
        for o in free[: self.garrison - len(self._garrison)]:
            if self._order("garrison", self.commander.capture, o.address, building.address, "d"):
                self._garrison[o.address] = [building.address, snap.frame, 0, o.type_id]
                self.claimed.add(o.address)
                self.log.append((snap.frame, "garrison", f"{o.type_id} -> {building.type_id}"))

    # ------------------------------------------------------------------- rebuild
    def _rebuild(self, snap: Snapshot, mine: list[ObjectState]) -> None:
        basics = self.db.basics(self.country)
        keys = [basics[k] for k in REBUILD if k in basics]
        have = Counter(o.type_id for o in mine if o.type_id in keys)
        for t in keys:
            self._peak[t] = max(self._peak[t], have[t])
        if not self.rebuild:
            return
        if self._rebuilding is not None:
            try:
                self._rebuilding.tick(snap)
            except BridgeError as e:
                self.log.append((snap.frame, "rebuild failed", str(e)))
                self._rebuilding = None
                return
            if self._rebuilding.done:
                self._rebuilding = None
            elif not self._rebuilding.events and snap.frame - self._rebuilding.deploy_frame > REBUILD_START:
                # Live 2026-11-09 (series_v10 game 2) the tech centre was rebuilt before the radar it
                # needs: the rebuild never started, nothing else was rebuilt and the base fell on $40k.
                self._blocked[self._rebuilding.sequence[0]] = snap.frame
                self.log.append((snap.frame, "rebuild blocked", self._rebuilding.sequence[0]))
                self._rebuilding = None
            return
        if not any(o.type_id in ("NACNST", "GACNST", "YACNST") for o in mine):
            return  # no construction yard: nothing can be built
        credits = snap.player.credits if snap.player else 0
        lost = next((t for t in keys if have[t] < self._peak[t]
                     and snap.frame - self._blocked.get(t, -10**9) > REBUILD_START), None)
        if lost is None or credits < self.db.technos[lost].cost:
            return
        ex = BuildExecutor(self.db, [lost], self.commander, placer=self.placer)
        ex.deploy_frame = snap.frame  # the yard stands: no MCV to deploy
        self._rebuilding = ex
        self.log.append((snap.frame, "rebuild", lost))
        try:
            ex.tick(snap)
        except BridgeError as e:
            self.log.append((snap.frame, "rebuild failed", str(e)))
            self._rebuilding = None
