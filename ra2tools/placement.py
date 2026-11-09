"""Where to put a finished building: spread out instead of the bridge's nearest valid cell.

Live 2026-11-09 (series_v10 game 2) one Lightning Storm razed the battle lab, radar, barracks and
two power plants in 150 frames: packed edge to edge, every building sat under the same cloud.
Buildings may stand at most `Adjacent` (2) cells from another, so each new one goes 2 cells from
its nearest neighbour where it can, which also leaves lanes for our units through the base.

A building's snapshot position is its top-left foundation cell + 0.5 (checked on series_v10
game 5: the 16 buildings' foundations from that rule never overlap), and PLACE takes that cell.
"""
from __future__ import annotations

from pathlib import Path

from .ini import Ini
from .memory import Snapshot

DATA = Path(__file__).resolve().parent.parent / "data"


def foundations(data_dir: Path = DATA) -> dict[str, tuple[int, int]]:
    """Building type -> (width, height) in cells, from rulesmd Image= and artmd Foundation=."""
    rules = Ini.from_bytes((data_dir / "rulesmd.ini").read_bytes())
    art = Ini.from_bytes((data_dir / "artmd.ini").read_bytes())
    out = {}
    for tid in rules.registry("BuildingTypes"):
        image = rules.get(tid, "Image") or tid
        w, _, h = (art.get(image, "Foundation") or "1x1").lower().partition("x")
        try:
            out[tid] = (int(w), int(h))
        except ValueError:
            out[tid] = (1, 1)  # Custom foundations
    return out


def _gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    """Free cells between two foundations (x, y, w, h) along the wider axis; -1 if they overlap."""
    dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]))
    dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]))
    return max(dx, dy)


def spread_cells(snap: Snapshot, type_id: str, sizes: dict[str, tuple[int, int]], gap: int = 2,
                 radius: int = 10, limit: int = 6) -> list[tuple[int, int]]:
    """Top-left cells to try for `type_id`, best first: up to `limit` exactly `gap` free cells
    from the nearest of our buildings, then up to `limit` at gap - 1 (should the game read
    Adjacent tighter), nearest the construction yard first. `radius` keeps them off the home ore
    field (15 cells south of the yard on our map). Empty without a yard."""
    ours = [o for o in snap.objects if o.owner == snap.player_index and o.kind == "building"
            and o.on_map and o.health > 0 and o.type_id in sizes]
    yard = next((o for o in ours if o.type_id.endswith("CNST")), None)
    if yard is None:
        return []
    rects = [(int(o.x), int(o.y), *sizes[o.type_id]) for o in ours]
    w, h = sizes.get(type_id, (1, 1))
    cx, cy = int(yard.x), int(yard.y)
    found: dict[int, list[tuple[float, tuple[int, int]]]] = {gap: [], gap - 1: []}
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            nearest = min(_gap((x, y, w, h), r) for r in rects)
            if nearest in found:
                found[nearest].append(((x + w / 2 - cx) ** 2 + (y + h / 2 - cy) ** 2, (x, y)))
    return [cell for g in (gap, gap - 1) for _, cell in sorted(found[g])[:limit]]


class Spreader:
    """BuildExecutor.placer: the cells to try for a finished building, best first ([] = let the
    bridge pick the nearest)."""

    def __init__(self, db, data_dir: Path = DATA):
        self.db = db
        self.sizes = foundations(data_dir)

    def __call__(self, snap: Snapshot, type_id: str) -> list[tuple[int, int]]:
        t = self.db.technos.get(type_id)
        free = self.db.technos.get(t.free_unit) if t is not None and t.free_unit else None
        if t is None or t.combat_weapons or (free is not None and free.harvester):
            return []  # defences face the enemy, refineries sit by the ore: the bridge's nearest cell
        return spread_cells(snap, type_id, self.sizes)
