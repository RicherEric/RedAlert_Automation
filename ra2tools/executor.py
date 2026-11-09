"""Execute an optimised build order in a live game: memory snapshots in, bridge commands out.

The executor mirrors the economy simulator: one queue per production category (building /
vehicle / infantry / aircraft), each running one item at a time in sequence order. Buildings are
placed as soon as they finish. An item counts as done when one more of it is on the map than
when its production was ordered, which is also what the simulator's "done" event means
(buildings include the placement delay).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .bridge import BridgeError
from .gamedata import GameData
from .memory import Snapshot

CATEGORIES = ("building", "vehicle", "infantry", "aircraft")
MCVS = ("AMCV", "SMCV", "PCV")


class Commander(Protocol):
    def produce(self, type_id: str) -> str: ...
    def place(self, type_id: str, cell: tuple[int, int] | None = None) -> tuple[int, int]: ...
    def abandon(self, type_id: str) -> str: ...
    def deploy(self, unit: int | str) -> str: ...


@dataclass
class _Job:
    type_id: str
    started: int
    baseline: int
    tries: int = 0      # placement orders for a finished building
    placed_at: int = -10**9
    seen: bool = False  # the factory has been seen producing it
    shown: bool = False  # a building: it has been seen in the factory


@dataclass
class BuildExecutor:
    db: GameData
    sequence: list[str]
    commander: Commander
    events: list[tuple[int, str, str]] = field(default_factory=list)  # (frame, kind, type)
    deploy_frame: int | None = None
    _heads: dict[str, int] = field(default_factory=lambda: {c: 0 for c in CATEGORIES})
    _jobs: dict[str, _Job | None] = field(default_factory=lambda: {c: None for c in CATEGORIES})
    lost_after: int = 150  # frames a building order may take to show up in the factory
    place_every: int = 60  # frames between placement orders while a finished building waits
    place_tries: int = 3   # then give up on it and free the queue (cancel_unplaceable)
    # Units finished in a row that never showed up on the map, per type (see tick).
    vanished: dict[str, int] = field(default_factory=dict)
    # (snapshot, building type) -> cells to try first (placement.Spreader); the bridge's nearest
    # valid cell when none is given or none of them takes.
    placer: object = None

    def __post_init__(self) -> None:
        self._queues = {c: [t for t in self.sequence if self.category(t) == c] for c in CATEGORIES}

    def category(self, type_id: str) -> str:
        kind = self.db.technos[type_id].kind
        return kind if kind in CATEGORIES else "vehicle"

    @property
    def done(self) -> bool:
        return all(self._jobs[c] is None and self._heads[c] >= len(self._queues[c]) for c in CATEGORIES)

    @staticmethod
    def owned(snap: Snapshot) -> dict[str, int]:
        """On-map counts only: objects still being built exist off the map."""
        out: dict[str, int] = {}
        for o in snap.objects:
            if o.owner == snap.player_index and o.health > 0 and o.on_map:
                out[o.type_id] = out.get(o.type_id, 0) + 1
        return out

    def tick(self, snap: Snapshot) -> None:
        owned = self.owned(snap)
        if self.deploy_frame is None:
            self._deploy_mcv(snap, owned)
            return
        factories = {f.category: f for f in snap.factories}
        for cat in CATEGORIES:
            job = self._jobs[cat]
            if job is None:
                self._start_next(cat, snap.frame, owned)
                continue
            f = factories.get(cat)
            # Units also need their factory to have moved on, so a refinery's free harvester
            # does not count as the harvester we ordered.
            busy = cat != "building" and f is not None and f.type_id == job.type_id
            job.seen |= busy
            # A unit whose factory moved on is done even if the count did not grow: in a fight
            # another of the same type may die meanwhile (and a hero can vanish on exit).
            if (owned.get(job.type_id, 0) > job.baseline or job.seen) and not busy:
                self.events.append((snap.frame, "done", job.type_id))
                if cat != "building":
                    # Live 2026-11-06 (series_v4 games 9, 10, 12) every conscript the barracks
                    # finished was deleted before reaching the map: 70 were paid for in a row.
                    grew = owned.get(job.type_id, 0) > job.baseline
                    self.vanished[job.type_id] = 0 if grew else self.vanished.get(job.type_id, 0) + 1
                self._jobs[cat] = None
                self._start_next(cat, snap.frame, owned)
            elif cat == "building":
                # Defences (BuildCat=Combat) come out of the game's separate defence queue: live
                # 2026-11-06 a finished flak cannon sat there unplaced, unseen in "building".
                f = next((g for g in (factories.get("building"), factories.get("defense"))
                          if g is not None and g.type_id == job.type_id), factories.get("building"))
                job.shown |= f is not None and f.type_id == job.type_id
                if f and f.type_id == job.type_id and f.progress >= 1.0:
                    if snap.frame - job.placed_at < self.place_every:
                        continue
                    if job.tries >= self.place_tries:
                        self._cancel(cat, job, snap.frame)
                        continue
                    # Live 2026-11-08 (series_v9 game 1) a 4th refinery never landed, ordered once:
                    # it sat ready for 18000 frames and held every other queue's WAIT with it.
                    job.tries += 1
                    job.placed_at = snap.frame
                    if self._place_spread(snap, job.type_id):
                        continue
                    try:
                        self.commander.place(job.type_id)
                    except BridgeError as e:
                        if "place" not in str(e) and "spot" not in str(e):
                            raise
                elif not job.shown and snap.frame - job.started > self.lost_after:
                    # Accepted but never built: live 2026-11-06 a defence ordered on the frame the war
                    # factory was placed never appeared, and the queue waited on it for 17000 frames.
                    self.events.append((snap.frame, "lost", job.type_id))
                    self._jobs[cat] = None
                    self._retry(cat, job)

    def _place_spread(self, snap: Snapshot, type_id: str) -> bool:
        """Try the placer's cells in order; True once one takes."""
        for cell in (self.placer(snap, type_id) if self.placer else []):
            try:
                self.commander.place(type_id, cell)
                return True
            except BridgeError as e:
                if "cannot place" not in str(e):
                    return False  # not finished, or gone: the plain order below reports it
        return False

    def _cancel(self, cat: str, job: _Job, frame: int) -> None:
        """A finished building that will not go down: cancel it (refunded) and move on."""
        try:
            self.commander.abandon(job.type_id)
        except BridgeError:
            pass
        self.events.append((frame, "cancel", job.type_id))
        self._jobs[cat] = None
        self._unplaceable(cat, job, frame)

    def _unplaceable(self, cat: str, job: _Job, frame: int) -> None:
        pass  # a fixed sequence just skips it

    def _deploy_mcv(self, snap: Snapshot, owned: dict[str, int]) -> None:
        cnst = {"NACNST", "GACNST", "YACNST"}
        if any(owned.get(c) for c in cnst):
            self.deploy_frame = snap.frame
            return
        mcv = next((o for o in snap.objects if o.owner == snap.player_index and o.on_map
                    and o.type_id in MCVS), None)
        if mcv is None:
            raise BridgeError("沒有建造廠也沒有基地車")
        self.commander.deploy(mcv.address)
        self.deploy_frame = snap.frame
        self.events.append((snap.frame, "deploy", mcv.type_id))

    def _retry(self, cat: str, job: _Job) -> None:
        self._heads[cat] -= 1  # order it again

    def _start_next(self, cat: str, frame: int, owned: dict[str, int]) -> None:
        if self._heads[cat] >= len(self._queues[cat]):
            return
        type_id = self._queues[cat][self._heads[cat]]
        try:
            self.commander.produce(type_id)
        except BridgeError as e:
            if "cannot build" in str(e):
                return  # prerequisite or factory not there yet; retry next tick
            raise
        self._heads[cat] += 1
        self._jobs[cat] = _Job(type_id, frame, owned.get(type_id, 0))
        self.events.append((frame, "start", type_id))


def compare(predicted: list[tuple[int, str, str]], actual: list[tuple[int, str, str]],
            offset: int = 0) -> list[dict]:
    """Pair the n-th start/done of each type in prediction and game; game frames minus offset."""
    def index(events):
        out: dict[tuple[str, str], list[int]] = {}
        for frame, kind, tid in events:
            if kind in ("start", "done"):
                out.setdefault((tid, kind), []).append(frame)
        return out

    p, a = index(predicted), index([(f - offset, k, t) for f, k, t in actual])
    rows, seen = [], {}
    for frame, kind, tid in predicted:
        if kind != "done":
            continue
        n = seen[tid] = seen.get(tid, -1) + 1
        row = {"type": tid, "n": n + 1, "pred_start": p[(tid, "start")][n], "pred_done": frame}
        starts, dones = a.get((tid, "start"), []), a.get((tid, "done"), [])
        row["start"] = starts[n] if n < len(starts) else None
        row["done"] = dones[n] if n < len(dones) else None
        row["diff"] = None if row["done"] is None else row["done"] - frame
        rows.append(row)
    return rows
