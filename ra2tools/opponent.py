"""What the AI has at each moment of a skirmish, measured from a live-game log (monitor.py watch
--log-objects): its combat units and buildings over time, both start positions and the units each
side starts with. Used to judge an attack at any frame (stage 6: winning, 破關).

Frames are game frames since the start of the match; `at()` holds the last sample beyond the end.
"""
from __future__ import annotations

import bisect
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gamedata import GameData

MCVS = {"AMCV", "SMCV", "PCV"}
TRANSPORT_PLANES = {"PDPLANE"}  # drops paratroopers, which are counted themselves
# Units that kill or take buildings outright (C4, capture, infiltration): a Nighthawk drop of Tanya
# took our construction yard and refinery in 40 frames (live vs hard, 2026-11-02).
SABOTEURS = {"TANY", "GHOST", "ENGINEER", "SENGINEER", "YENGINEER"}  # spies only steal or blind: not a raid


@dataclass
class Opponent:
    name: str
    country: str               # the AI's country
    player_country: str
    frames: list[int]
    units: list[dict[str, int]]      # AI combat units at each frame (harvesters left out)
    buildings: list[dict[str, int]]  # AI buildings at each frame
    base_distance: float             # cells between the two bases' centres
    player_start_army: dict[str, int] = field(default_factory=dict)  # the skirmish's free units
    source: str = ""
    difficulty: str = ""             # easy / medium / hard (HouseClass::AIDifficulty)
    # AI attack waves on our base: [{"frame": first sighting, "units": most of each type seen}]
    attacks: list[dict] = field(default_factory=list)
    # How the AI answers what we build, measured live, e.g. {"air": {"delay": 1550, "every": 1100,
    # "max": 6, "add": {"NASAM": 1}}}: `delay` frames after our first aircraft it adds `add`, then
    # again every `every` frames, at most `max` times. Timelines recorded against a tank army
    # never show these buildings.
    responses: dict = field(default_factory=dict)
    # Credits per frame the AI spends replacing units lost while we attack its base, measured
    # from recorded attacks (None: unlimited, 0: none). A passive timeline never shows this.
    replace_rate: float | None = 0.0
    notes: dict = field(default_factory=dict)  # where hand-set values came from
    # Saboteur raids on our base: [{"frame": first saboteur near it, "units": what came with them}].
    # Fought like a wave, but our base is lost unless its defenders also kill the saboteurs fast.
    raids: list[dict] = field(default_factory=list)

    def response(self, kind: str, trigger: float, frame: float) -> dict[str, int]:
        """Buildings added by the `kind` response by `frame`, if it was triggered at `trigger`."""
        r = self.responses.get(kind)
        if r is None or frame < trigger + r["delay"]:
            return {}
        n = min(r.get("max", 99), 1 + int((frame - trigger - r["delay"]) // r["every"]))
        return {t: k * n for t, k in r["add"].items()}

    def at(self, frame: float) -> tuple[dict[str, int], dict[str, int]]:
        i = max(0, bisect.bisect_right(self.frames, frame) - 1)
        return dict(self.units[i]), dict(self.buildings[i])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Opponent":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


def _is_combat_unit(db: GameData, tid: str) -> bool:
    t = db.technos.get(tid)
    return t is not None and t.kind != "building" and bool(t.combat_weapons) and not t.harvester \
        and tid not in MCVS and tid not in TRANSPORT_PLANES


ATTACK_RADIUS = 35.0   # AI combat units this close to our base centre are attacking it
RAID_RADIUS = 20.0     # saboteurs this close to our base centre are raiding it
WAVE_GAP = 1200        # frames without attackers that end a wave


def from_log(db: GameData, path: Path, name: str, every: int = 300, house: int | None = None,
             until_attacked: bool = False) -> Opponent:
    """Sample the AI house (the first non-human, non-neutral one unless `house`) every `every` frames.

    `until_attacked` stops at the first sample with our combat units near the AI base: from then
    on the timeline shows our attack, not what the AI fields on its own (for quantile())."""
    frames, units, buildings = [], [], []
    ai = player = None
    centres: dict[int, tuple[float, float]] = {}
    start_army: dict[str, int] = {}
    attacks: list[dict] = []
    last_attack = -WAVE_GAP
    raids: list[dict] = []
    last_raid = -WAVE_GAP
    last = -every
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "objects" not in r:
                continue
            if ai is None:
                houses = [h for h in r["houses"] if h["country"] not in ("Neutral", "Special")]
                ai = next(h for h in houses if (h["index"] == house if house is not None else not h["is_human"]))
                player = next(h for h in houses if h["index"] == r["player_index"])
            mine = Counter(o["type_id"] for o in r["objects"]
                           if o["owner"] == player["index"] and o["on_map"] and o["health"] > 0)
            if not start_army and mine:
                start_army = {t: n for t, n in mine.items() if _is_combat_unit(db, t)}
            for h in (ai, player):  # base centre: where the buildings are once the MCV has deployed
                pts = [(o["x"], o["y"]) for o in r["objects"] if o["owner"] == h["index"] and o["on_map"]
                       and o["health"] > 0 and o["kind"] == "building"]
                if pts and h["index"] not in centres:
                    centres[h["index"]] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            if r["frame"] - last < every:
                continue
            there = centres.get(ai["index"])
            if until_attacked and there and any(
                    o["owner"] == player["index"] and o["on_map"] and o["health"] > 0
                    and _is_combat_unit(db, o["type_id"]) and math.dist((o["x"], o["y"]), there) <= ATTACK_RADIUS
                    for o in r["objects"]):
                break
            last = r["frame"]
            c = Counter(o["type_id"] for o in r["objects"]
                        if o["owner"] == ai["index"] and o["on_map"] and o["health"] > 0 and o["type_id"] in db.technos)
            frames.append(int(r["frame"]))
            units.append({t: n for t, n in c.items() if _is_combat_unit(db, t)})
            buildings.append({t: n for t, n in c.items() if db.technos[t].kind == "building"})
            home = centres.get(player["index"])
            near = Counter(o["type_id"] for o in r["objects"]
                           if home and o["owner"] == ai["index"] and o["on_map"] and o["health"] > 0
                           and _is_combat_unit(db, o["type_id"]) and math.dist((o["x"], o["y"]), home) <= ATTACK_RADIUS)
            if near:
                if r["frame"] - last_attack >= WAVE_GAP:
                    attacks.append({"frame": int(r["frame"]), "units": {}})
                wave = attacks[-1]["units"]
                for t, n in near.items():
                    wave[t] = max(wave.get(t, 0), n)
                last_attack = r["frame"]
            close = Counter(o["type_id"] for o in r["objects"]
                            if home and o["owner"] == ai["index"] and o["on_map"] and o["health"] > 0
                            and (o["type_id"] in SABOTEURS or _is_combat_unit(db, o["type_id"]))
                            and math.dist((o["x"], o["y"]), home) <= RAID_RADIUS)
            if any(t in SABOTEURS for t in close) or (close and r["frame"] - last_raid < WAVE_GAP):
                if r["frame"] - last_raid >= WAVE_GAP:
                    raids.append({"frame": int(r["frame"]), "units": {}})
                raid = raids[-1]["units"]
                for t, n in close.items():
                    raid[t] = max(raid.get(t, 0), n)
                if any(t in SABOTEURS for t in close):
                    last_raid = r["frame"]
    if ai is None or not frames:
        raise ValueError(f"{path}: no object records (record with monitor.py watch --log-objects)")
    a, b = centres.get(ai["index"]), centres.get(player["index"])
    distance = math.dist(a, b) if a and b else 0.0
    return Opponent(name, ai["country"], player["country"], frames, units, buildings, distance,
                    start_army, str(path), ai.get("ai_difficulty") or "", attacks, raids=raids)


# ------------------------------------------------------------------ quantile opponent (paper)
SLOT = 3600  # frames per time slot: 3 minutes at 20 fps, the paper's periods


def threat(db: GameData, units: dict[str, int], buildings: dict[str, int]) -> float:
    """What games are ranked by: credits of combat units plus armed buildings."""
    return sum(db.technos[t].cost * n for t, n in units.items()) \
        + sum(db.technos[t].cost * n for t, n in buildings.items() if db.technos[t].combat_weapons)


def _rank(n: int, p: float) -> int:
    """Index in ascending order of the p-quantile of n samples: the ceil(n*p)-th smallest."""
    return min(n - 1, max(0, math.ceil(n * p) - 1))


def quantile(db: GameData, games: list[Opponent], p: float, name: str, template: Opponent | None = None,
             slot: int = SLOT, every: int = 300) -> Opponent:
    """An AI at least as strong as a share p of the recorded games, after Wang et al. (CCC 2019):
    there the threshold xi is the ceil(N*p)-th of N players ranked by the enemy units they saw.

    Each `slot` of the game takes its timeline from the game ranked ceil(N*p)-th by mean threat
    over that slot, among the games recorded through it (from_log(until_attacked=True) ends a
    record when our attack reaches the AI). The k-th attack wave is kept when at least a share
    1-p of the games that lasted that long had one; it comes at the (1-p)-quantile frame (early
    is dangerous) with the p-quantile army. Buildings the AI adds only in answer to our army
    (template.responses) are left out, VictoryProblem adds them when they are triggered.
    Everything not measured per game (responses, replace_rate, start army) comes from `template`."""
    template = template or games[0]
    answers = {t for r in template.responses.values() for t in r.get("add", {})}
    end = max(g.frames[-1] for g in games)
    frames, units, buildings, picked = [], [], [], []
    for s in range(end // slot + 1):
        lo, hi = s * slot, min((s + 1) * slot, end + 1)
        grid = list(range(lo - lo % every + (every if lo % every else 0), hi, every)) or [lo]
        covering = [g for g in games if g.frames[-1] >= min(lo + slot // 2, end)] or [max(games, key=lambda g: g.frames[-1])]
        strip = lambda b: {t: n for t, n in b.items() if t not in answers}
        score = lambda g: sum(threat(db, g.at(f)[0], strip(g.at(f)[1])) for f in grid) / len(grid)
        g = sorted(covering, key=score)[_rank(len(covering), p)]
        picked.append({"slot": s, "game": g.source, "of": len(covering), "threat": round(score(g))})
        for f in grid:
            u, b = g.at(f)
            frames.append(f)
            units.append(u)
            buildings.append(strip(b))
    attacks, prev = [], -1
    for k in range(max(len(g.attacks) for g in games)):
        waves = [g.attacks[k] for g in games if len(g.attacks) > k]
        first = min(w["frame"] for w in waves)
        lasted = [g for g in games if len(g.attacks) > k or g.frames[-1] >= first]
        if len(waves) < (1 - p) * len(lasted):
            break
        when = sorted(w["frame"] for w in waves)[_rank(len(waves), 1 - p)]
        army = sorted(waves, key=lambda w: threat(db, w["units"], {}))[_rank(len(waves), p)]["units"]
        attacks.append({"frame": max(when, prev + 1), "units": dict(army)})
        prev = attacks[-1]["frame"]
    raids, prev = [], -1
    for k in range(max(len(g.raids) for g in games)):
        hits = [g.raids[k] for g in games if len(g.raids) > k]
        first = min(w["frame"] for w in hits)
        lasted = [g for g in games if len(g.raids) > k or g.frames[-1] >= first]
        if len(hits) < (1 - p) * len(lasted):
            break
        when = sorted(w["frame"] for w in hits)[_rank(len(hits), 1 - p)]
        army = sorted(hits, key=lambda w: threat(db, w["units"], {}))[_rank(len(hits), p)]["units"]
        raids.append({"frame": max(when, prev + 1), "units": dict(army)})
        prev = raids[-1]["frame"]
    return Opponent(
        name, template.country, template.player_country, frames, units, buildings, template.base_distance,
        dict(template.player_start_army), ", ".join(g.source for g in games), template.difficulty, attacks,
        dict(template.responses), template.replace_rate,
        {**template.notes, "quantile": {"p": p, "games": len(games), "slots": picked}}, raids=raids)


def extend_with(g: Opponent, tail: Opponent) -> Opponent:
    """`g` continued past its last sample by what `tail` adds after that frame (its later
    samples minus its own sample there), attacks and raids, instead of at() holding g's last
    sample for the rest of the match (an AI frozen in time).

    Until 2026-11-09 the later samples replaced g's outright: most records end when our army
    reaches the AI (~15-22k), so every game turned into the one long record from then on, whose
    AI fields only $6-10k of units, and armies arriving late looked like easy wins (series_v10:
    predicted 51%, live 0 of 4). Adding the growth keeps each game's own strength."""
    from dataclasses import replace
    end = g.frames[-1]
    k = bisect.bisect_right(tail.frames, end)
    if k >= len(tail.frames):
        return g
    base_u, base_b = tail.at(end)
    grow = lambda own, now, then: {t: n for t in set(own) | set(now)
                                   if (n := own.get(t, 0) + now.get(t, 0) - then.get(t, 0)) > 0}
    units = [grow(g.units[-1], u, base_u) for u in tail.units[k:]]
    buildings = [grow(g.buildings[-1], b, base_b) for b in tail.buildings[k:]]
    later = lambda events: [e for e in events if e["frame"] > end]
    return replace(g, frames=g.frames + tail.frames[k:], units=g.units + units,
                   buildings=g.buildings + buildings, attacks=g.attacks + later(tail.attacks),
                   raids=g.raids + later(tail.raids),
                   notes={**g.notes, "extended": f"after frame {end} from {tail.name}"})


def harden(g: Opponent, scale: float = 1.0, earlier: float = 0.0) -> Opponent:
    """`g` with every attack wave and raid `scale` times as big (rounded half up, never smaller)
    and `earlier` frames sooner: a safety margin, since the hard AI's waves differ from game to
    game and plans that just held the recorded ones lost the games they had not seen."""
    from dataclasses import replace
    if scale == 1.0 and not earlier:
        return g
    grow = lambda u: {t: max(n, math.floor(n * scale + 0.5)) for t, n in u.items()}
    move = lambda events: [{**e, "frame": max(0, int(e["frame"] - earlier)), "units": grow(e["units"])}
                           for e in events]
    return replace(g, attacks=move(g.attacks), raids=move(g.raids),
                   notes={**g.notes, "hardened": {"scale": scale, "earlier": earlier}})


def games_from_logs(db: GameData, paths: list[str], template: Opponent, min_frames: int = 16000,
                    extend: bool = True) -> list[Opponent]:
    """Each recorded game as its own opponent (VictoryProblem robust mode, per-game checks), with
    what a single record cannot show from `template` (responses, replace rate, start army, base
    distance) and the response buildings left out, as in quantile(). Games recorded for less than
    `min_frames` are skipped. With `extend` the shorter games go on as the longest one does
    (extend_with): a $30k army is only ready around frame 30000, when most records have ended."""
    from dataclasses import replace
    answers = {t for r in template.responses.values() for t in r.get("add", {})}
    out = []
    for path in paths:
        g = from_log(db, Path(path), Path(path).stem, until_attacked=True)
        if g.frames[-1] < min_frames:
            continue
        out.append(replace(g, buildings=[{t: n for t, n in b.items() if t not in answers} for b in g.buildings],
                           base_distance=template.base_distance, player_start_army=dict(template.player_start_army),
                           responses=dict(template.responses), replace_rate=template.replace_rate,
                           difficulty=template.difficulty))
    if extend and out:
        longest = max(out, key=lambda g: g.frames[-1])
        out = [extend_with(g, longest) for g in out]
    return out

