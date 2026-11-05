"""Turn in-game stopwatch measurements into Calibration values.

Every measurement is a list of intervals in wall-clock seconds. The frame rate is anchored on
build times (known in frames from rulesmd.ini); everything else is converted to frames with it.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gamedata import GameData

# How the marks of each test are grouped into intervals:
#   pairs    -> (start, end), (start, end), ...   one interval per pair
#   sequence -> every consecutive gap is an interval
TESTS: dict[str, dict] = {
    "build": {
        "marks": "pairs",
        "needs": ["item"],
        "title": "建造時間 → fps",
        "howto": [
            "滿電力、只有 1 座對應工廠、錢夠用（避免低電力或缺錢拖慢）",
            "點下建造圖示的瞬間按【標記】，聽到「建造完成」或圖示顯示完成時再按一次",
            "建議量 2~3 種不同價格的東西（例如電廠、兵營、精鍊廠），用來驗證公式",
        ],
    },
    "move": {
        "marks": "pairs",
        "needs": ["unit", "cells"],
        "title": "移動速度 → speed_to_cells",
        "howto": [
            "先蓋一排直線城牆當尺（1 段 = 1 格），例如 10 段",
            "讓單位在城牆旁邊平地上直線前進：經過第一段時按【標記】，經過最後一段時再按一次",
            "--cells 填實際經過的格數（10 段城牆從頭到尾 = 9 格，從第 1 段起點到第 10 段終點 = 10 格）",
        ],
    },
    "harvest": {
        "marks": "sequence",
        "needs": ["unit"],
        "title": "採礦循環 → ore_distance / 採礦 / 卸礦時間",
        "howto": [
            "只盯一台礦車（一般地圖、礦區離精鍊廠距離正常即可）",
            "依序按【標記】：①離開精鍊廠 ②抵達礦區開始採 ③採滿離開 ④回到精鍊廠開始卸 ⑤卸完離開",
            "可以連續量好幾趟：⑤ 就是下一趟的 ①，繼續每個階段按一次即可",
            "超時空採礦車的 ③→④ 是傳送，時間會很短，照樣標記",
        ],
    },
    "deploy": {
        "marks": "pairs",
        "needs": [],
        "title": "基地車展開 → mcv_deploy_frames",
        "howto": ["遊戲開始畫面出現時按【標記】，建造廠展開完成、可以建造時再按一次"],
    },
    "place": {
        "marks": "pairs",
        "needs": [],
        "title": "擺放建築延遲 → place_delay_frames",
        "howto": ["建築「建造完成」時按【標記】，你實際把它擺下去時再按一次。用你平常的速度，量個幾次"],
    },
    "reload": {
        "marks": "pairs",
        "needs": ["unit"],
        "title": "飛機補彈往返 → aircraft_reload_frames",
        "howto": ["飛機打完彈藥開始返航時按【標記】，補完彈飛回同一個目標再次開火時再按一次"],
    },
    "slave": {
        "marks": "sequence",
        "needs": [],
        "title": "奴隸礦工循環 → slave_cycle_frames",
        "howto": ["只盯一個奴隸礦工：每次它把礦交回奴隸礦場時按一次【標記】"],
    },
}

FIT_ORDER = ["fps", "speed_to_cells", "ore_distance", "harvest_frames_per_bale", "unload_frames",
             "chrono_return_frames", "ore_home_credits", "ore_far_distance", "harvest_efficiency", "mcv_deploy_frames", "place_delay_frames",
             "bot_mcv_deploy_frames", "bot_place_delay_frames",
             "aircraft_reload_frames", "slave_cycle_frames"]


@dataclass
class Measurement:
    test: str
    intervals: list[float]  # seconds
    params: dict = field(default_factory=dict)
    game_speed: str | None = None
    recorded: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


def marks_to_intervals(test: str, marks: list[float]) -> list[float]:
    if TESTS[test]["marks"] == "pairs":
        return [b - a for a, b in zip(marks[0::2], marks[1::2])]
    return [b - a for a, b in zip(marks, marks[1:])]


class MeasurementStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[Measurement]:
        if not self.path.exists():
            return []
        return [Measurement(**m) for m in json.loads(self.path.read_text(encoding="utf-8"))]

    def save(self, items: list[Measurement]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(m) for m in items], ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def add(self, m: Measurement) -> int:
        items = self.load()
        items.append(m)
        self.save(items)
        return len(items) - 1


@dataclass
class FitResult:
    values: dict[str, float]
    samples: dict[str, list[float]]
    notes: list[str]
    fps_by_speed: dict[str, float] = field(default_factory=dict)  # game-speed setting -> fps


def _median(xs: list[float]) -> float:
    return float(statistics.median(xs))


def fit(db: GameData, measurements: list[Measurement]) -> FitResult:
    samples: dict[str, list[float]] = {}
    notes: list[str] = []
    by_test: dict[str, list[Measurement]] = {}
    for m in measurements:
        by_test.setdefault(m.test, []).append(m)

    speeds = {m.game_speed for m in measurements if m.game_speed}
    if len(speeds) > 1:
        notes.append(f"警告：量測混用了不同遊戲速度 {sorted(speeds)}，fps 會不準；請只保留同一種速度")

    # 1) fps from build times (frames are known exactly from rules).
    per_item: dict[str, list[float]] = {}
    for m in by_test.get("build", []):
        t = db.technos.get(m.params.get("item", ""))
        if t is None:
            notes.append(f"略過未知的建造項目 {m.params.get('item')}")
            continue
        frames = db.build_frames(t)
        for sec in m.intervals:
            if sec > 0:
                per_item.setdefault(t.id, []).append(frames / sec)
    if per_item:
        samples["fps"] = [x for xs in per_item.values() for x in xs]
        for item, xs in per_item.items():
            notes.append(f"fps 依 {item}: {', '.join(f'{x:.1f}' for x in xs)}")
        item_fps = [_median(xs) for xs in per_item.values()]
        if len(item_fps) > 1 and (max(item_fps) - min(item_fps)) / _median(item_fps) > 0.1:
            notes.append("警告：不同建造項目算出的 fps 差超過 10%，建造時間公式可能需要修正")
    fps = _median(samples["fps"]) if "fps" in samples else None
    if fps is None:
        notes.append("沒有 build 量測，無法換算幀數；其他參數全部略過")
        return FitResult({}, samples, notes)
    values: dict[str, float] = {"fps": fps}

    # 2) speed_to_cells from timed straight-line moves.
    for m in by_test.get("move", []):
        t = db.technos.get(m.params.get("unit", ""))
        cells = float(m.params.get("cells", 0))
        if t is None or not t.speed or cells <= 0:
            notes.append(f"略過 move 量測（單位或格數無效）：{m.params}")
            continue
        for sec in m.intervals:
            samples.setdefault("speed_to_cells", []).append(cells / (sec * fps * t.speed))
    if "speed_to_cells" in samples:
        values["speed_to_cells"] = _median(samples["speed_to_cells"])

    # 3) Harvester cycle phases: to field, loading, back, unloading.
    stc = values.get("speed_to_cells")
    for m in by_test.get("harvest", []):
        t = db.technos.get(m.params.get("unit", ""))
        if t is None:
            continue
        cycles = [m.intervals[i:i + 4] for i in range(0, len(m.intervals) - 3, 4)]
        if len(m.intervals) % 4:
            notes.append(f"harvest 量測有 {len(m.intervals) % 4} 段不足一個完整循環，已略過")
        for to_field, loading, back, unloading in cycles:
            if stc:
                samples.setdefault("ore_distance", []).append(to_field * fps * t.speed * stc)
            elif "ore_distance" not in values:
                notes.append("ore_distance 需要先有 move 量測")
            if t.storage:
                samples.setdefault("harvest_frames_per_bale", []).append(loading * fps / t.storage)
            samples.setdefault("unload_frames", []).append(unloading * fps)
            if t.teleporter:
                samples.setdefault("chrono_return_frames", []).append(back * fps)
    for key in ("ore_distance", "harvest_frames_per_bale", "unload_frames", "chrono_return_frames"):
        if key in samples:
            values[key] = _median(samples[key])

    # 4) Plain durations.
    for test, key in (("deploy", "mcv_deploy_frames"), ("place", "place_delay_frames"),
                      ("reload", "aircraft_reload_frames"), ("slave", "slave_cycle_frames")):
        xs = [sec * fps for m in by_test.get(test, []) for sec in m.intervals]
        if xs:
            samples[key] = xs
            values[key] = _median(xs)
    return FitResult(values, samples, notes)


def _factory_counts(db: GameData, record: dict) -> dict[str, int]:
    """Player's finished factories per queue category, from objects (or counts) in a log record."""
    pa = record.get("player_index")
    if record.get("objects"):
        owned: dict[str, int] = {}
        for o in record["objects"]:
            if o["owner"] == pa and o["on_map"] and o["kind"] == "building":
                owned[o["type_id"]] = owned.get(o["type_id"], 0) + 1
    else:
        owned = record.get("counts", {}).get(str(pa), {})
    out: dict[str, int] = {}
    for tid, n in owned.items():
        t = db.technos.get(tid)
        if t and t.factory:
            out[t.factory] = out.get(t.factory, 0) + n
    return out


def fit_log(db: GameData, records: list[dict]) -> FitResult:
    """Calibrate from a monitor.py watch log (frame counter + factory progress)."""
    samples: dict[str, list[float]] = {}
    notes: list[str] = []
    running: dict[int | None, list[float]] = {}  # game-speed setting -> [frames, seconds]
    # One build run = consecutive samples of the same item in the same factory. Summing frames and
    # progress over the whole run avoids the 1/54-step quantisation bias of per-sample slopes.
    runs: dict[str, list] = {}  # category -> [type_id, factories, frames, progress] of the current run
    slopes: dict[tuple[str, str, int], list[float]] = {}  # (category, type, factories) -> frames per build
    low_power_skipped = 0

    def close(cat: str) -> None:
        run = runs.pop(cat, None)
        if run and run[3] >= 0.2:  # skip short runs: too few steps to be accurate
            slopes.setdefault((cat, run[0], run[1]), []).append(run[2] / run[3])

    for a, b in zip(records, records[1:]):
        d_frame = b["frame"] - a["frame"]
        d_wall = b["wall_time"] - a["wall_time"]
        if d_frame <= 0 or d_wall <= 0:
            continue  # paused, menu, or a new game
        if a.get("game_speed") != b.get("game_speed"):
            continue  # the setting changed inside this interval
        acc = running.setdefault(b.get("game_speed"), [0.0, 0.0])
        acc[0] += d_frame
        acc[1] += d_wall
        fa = {f["category"]: f for f in a.get("factories", [])}
        factories = _factory_counts(db, b)
        player = next((h for h in b.get("houses", []) if h["index"] == b.get("player_index")), None)
        low_power = bool(player and player["power_drain"] > player["power_output"])
        active = set()
        for f in b.get("factories", []):
            cat = f["category"]
            g = fa.get(cat)
            if not g or not f["type_id"] or f["type_id"] != g["type_id"] or f["progress"] < g["progress"]:
                continue
            # Only strictly-inside samples: start/finish ticks would bias the rate.
            if f["on_hold"] or g["on_hold"] or not (0 < g["progress"] and f["progress"] < 1):
                continue
            if low_power:
                low_power_skipped += 1
                continue
            n = factories.get(cat, 1)
            run = runs.get(cat)
            if run is None or run[0] != f["type_id"] or run[1] != n:
                close(cat)
                run = runs[cat] = [f["type_id"], n, 0.0, 0.0]
            run[2] += d_frame
            run[3] += f["progress"] - g["progress"]
            active.add(cat)
        for cat in list(runs):
            if cat not in active:
                close(cat)
    for cat in list(runs):
        close(cat)
    values: dict[str, float] = {}
    by_speed: dict[str, float] = {}
    for speed, (frames, secs) in sorted(running.items(), key=lambda kv: -kv[1][1]):
        fps = frames / secs
        if "fps" not in values:  # the setting played the longest becomes the default
            values["fps"] = fps
            samples["fps"] = [fps]
        label = "未知（舊日誌）" if speed is None else str(speed)
        notes.append(f"fps（速度設定 {label}）：{frames:.0f} 幀 / {secs:.1f} 秒 = {fps:.2f}（已排除暫停）")
        if speed is not None:
            by_speed[str(speed)] = fps
    if not running:
        notes.append("日誌裡沒有可用的遊戲進行片段")
    if low_power_skipped:
        notes.append(f"略過 {low_power_skipped} 筆低電力時的建造樣本")
    for (cat, tid, n), xs in sorted(slopes.items()):
        t = db.technos.get(tid)
        measured = _median(xs)
        if t is None:
            continue
        expected = db.build_frames(t, n)
        ratio = measured / expected
        where = f"（{n} 座工廠）" if n > 1 else ""
        hint = "（可能是缺錢，或工廠在建造途中增減）" if abs(ratio - 1) > 0.05 else ""
        notes.append(f"建造 {tid}{where}: 實測 {measured:.0f} 幀 / 規則公式 {expected:.0f} 幀 = {ratio:.2f}{hint}")
        samples.setdefault("build_ratio", []).append(ratio)
    return FitResult(values, samples, notes, by_speed)


def write_toml(path: Path, result: FitResult, merge: bool = False) -> None:
    import tomllib

    values = dict(result.values)
    by_speed = dict(result.fps_by_speed)
    if merge and path.exists():
        old = tomllib.loads(path.read_text(encoding="utf-8"))
        by_speed = {**old.pop("fps_by_speed", {}), **by_speed}
        values = {**old, **values}
    lines = ["# 由 calibrate.py fit / auto 產生；ra2opt.py 會自動載入（情境檔的 [calibration] 可再覆寫）",
             f"# 產生時間 {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for key in FIT_ORDER:
        if key in values:
            n = len(result.samples.get(key, []))
            note = f"  # {n} 筆樣本" if n else ""
            lines.append(f"{key} = {values[key]:.4g}{note}")
    if by_speed:
        lines += ["", "# 遊戲速度設定 -> 實測 fps（monitor.py 讀到的 GameSpeed）；情境檔可用 game_speed = N 指定",
                  "[fps_by_speed]"]
        lines += [f'"{k}" = {v:.4g}' for k, v in sorted(by_speed.items(), key=lambda kv: int(kv[0]))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- object tracks
SKIP_HOUSES = {"Neutral", "Special"}


def _tracks(records: list[dict]) -> dict[int, list[tuple[int, float, float, str, str]]]:
    """Object address -> [(frame, x, y, type_id, kind)] for every non-neutral object on the map."""
    tracks: dict[int, list] = {}
    for r in records:
        neutral = {h["index"] for h in r["houses"] if h["country"] in SKIP_HOUSES}
        for o in r.get("objects", []):
            if o["owner"] in neutral or not o["on_map"] or o["health"] <= 0:
                continue
            tr = tracks.setdefault(o["address"], [])
            if tr and (tr[-1][3] != o["type_id"] or r["frame"] <= tr[-1][0]):
                tr.clear()  # address reused by a new object, or a new game
            tr.append((r["frame"], o["x"], o["y"], o["type_id"], o["kind"]))
    return tracks


def _cruise_speeds(track: list) -> list[tuple[float, float]]:
    """(cells, frames) of each straight cruising run, first and last step dropped (acceleration)."""
    out = []
    run: list[tuple[float, float, float, float]] = []  # (dx, dy, dist, frames)

    def flush() -> None:
        inner = run[1:-1]
        if len(inner) >= 2:
            out.append((sum(s[2] for s in inner), sum(s[3] for s in inner)))
        run.clear()

    for (f0, x0, y0, *_), (f1, x1, y1, *_) in zip(track, track[1:]):
        dx, dy, df = x1 - x0, y1 - y0, f1 - f0
        dist = (dx * dx + dy * dy) ** 0.5
        if df <= 0 or dist / df < 0.005:
            flush()
            continue
        if run:
            px, py, pd, _ = run[-1]
            if (px * dx + py * dy) / (pd * dist) < 0.98:  # direction changed
                flush()
        run.append((dx, dy, dist, df))
    flush()
    return out


def _segments(labels: list[str]) -> list[tuple[str, int, int]]:
    out = []
    for i, lab in enumerate(labels):
        if out and out[-1][0] == lab:
            out[-1] = (lab, out[-1][1], i + 1)
        else:
            out.append((lab, i, i + 1))
    return out


def fit_objects(db: GameData, records: list[dict], dock_radius: float = 4.5) -> FitResult:
    """Calibrate movement, harvesting and placement delay from a `watch --log-objects` log."""
    samples: dict[str, list[float]] = {}
    notes: list[str] = []
    values: dict[str, float] = {}
    tracks = _tracks(records)

    # 1) speed_to_cells: straight cruising runs of ground units.
    per_type: dict[str, list[tuple[float, float]]] = {}
    for tr in tracks.values():
        t = db.technos.get(tr[0][3])
        if t is None or tr[0][4] not in ("vehicle", "infantry") or not t.speed or t.is_air:
            continue
        per_type.setdefault(t.id, []).extend(_cruise_speeds(tr))
    for tid, runs in sorted(per_type.items()):
        cells = sum(c for c, _ in runs)
        frames = sum(f for _, f in runs)
        if cells < 8:
            continue  # too little straight-line driving to trust
        t = db.technos[tid]
        stc = cells / frames / t.speed
        samples.setdefault("speed_to_cells", []).append(stc)
        notes.append(f"移動 {tid}(Speed={t.speed}): {cells:.0f} 格 / {frames:.0f} 幀 → {stc:.4f}")
    if "speed_to_cells" in samples:
        values["speed_to_cells"] = _median(samples["speed_to_cells"])
    else:
        notes.append("沒有足夠的直線移動資料，speed_to_cells 略過")
    stc = values.get("speed_to_cells")

    # 2) Harvest cycles: in the dock zone (unload) -> out -> working (load) -> back -> dock zone.
    # Only the player's own base: other houses have other ore fields and harvester types.
    me = records[-1].get("player_index")
    refineries = [(o["x"], o["y"]) for o in records[-1].get("objects", [])
                  if o["kind"] == "building" and o["owner"] == me and (b := db.technos.get(o["type_id"]))
                  and b.free_unit and (h := db.technos.get(b.free_unit)) and h.harvester]
    mine = {o["address"] for r in records for o in r.get("objects", []) if o["owner"] == me}
    for addr, tr in tracks.items():
        t = db.technos.get(tr[0][3])
        if addr not in mine:
            continue
        if t is None or not t.harvester or t.kind != "vehicle" or not refineries:
            continue
        labels = []
        for (f0, x0, y0, *_), (f1, x1, y1, *_) in zip(tr, tr[1:]):
            near = min((x1 - rx) ** 2 + (y1 - ry) ** 2 for rx, ry in refineries) ** 0.5 < dock_radius
            moving = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / max(f1 - f0, 1) > 0.02
            labels.append("dock" if near else ("move" if moving else "work"))
        frames_of = lambda a, b: tr[b][0] - tr[a][0]  # step i spans samples i..i+1
        segs = _segments(labels)
        # A trip is the stretch between two dock visits; split it at the first/last "work" step.
        docks = [s for s in segs if s[0] == "dock"]
        for d0, d1 in zip(docks, docks[1:]):
            trip = labels[d0[2]:d1[1]]
            if "work" not in trip:
                continue
            first = d0[2] + trip.index("work")
            last = d0[2] + len(trip) - 1 - trip[::-1].index("work")
            out_f, load_f, back_f = frames_of(d0[2], first), frames_of(first, last + 1), frames_of(last + 1, d1[1])
            unload_f = frames_of(d1[1], d1[2])
            if t.teleporter:
                samples.setdefault("chrono_return_frames", []).append(back_f)
            if stc:
                samples.setdefault("ore_distance", []).append(out_f * t.speed * stc)
            if t.storage:
                samples.setdefault("harvest_frames_per_bale", []).append(load_f / t.storage)
            if d1 is not docks[-1]:  # the last dock visit may be cut off by the end of the log
                samples.setdefault("unload_frames", []).append(unload_f)
    trips = len(samples.get("harvest_frames_per_bale", []))
    if trips:
        notes.append(f"採礦：分析了 {trips} 趟完整循環")
    else:
        notes.append("沒有完整的採礦循環（礦車要至少來回一趟），採礦參數略過")
    for key in ("ore_distance", "harvest_frames_per_bale", "unload_frames", "chrono_return_frames"):
        if key in samples:
            values[key] = _median(samples[key])

    # 3) Placement delay: building finished in the factory -> appears on the map.
    pending: dict[str, int] = {}  # type_id -> estimated completion frame
    for a, b in zip(records, records[1:]):
        fa = {f["category"]: f for f in a.get("factories", [])}
        fb = {f["category"]: f for f in b.get("factories", [])}
        g, f = fa.get("building"), fb.get("building")
        if g and f and g["type_id"] and g["type_id"] == f["type_id"] and g["progress"] < 1 <= f["progress"]:
            t = db.technos.get(f["type_id"])
            if t:
                pending[t.id] = round(a["frame"] + (1 - g["progress"]) * db.build_frames(t))
        pa = a.get("player_index")
        for tid, done in list(pending.items()):
            n_a = sum(1 for o in a.get("objects", []) if o["owner"] == pa and o["type_id"] == tid and o["on_map"])
            n_b = sum(1 for o in b.get("objects", []) if o["owner"] == pa and o["type_id"] == tid and o["on_map"])
            if n_b > n_a and b["frame"] >= done:
                samples.setdefault("place_delay_frames", []).append(b["frame"] - done)
                del pending[tid]
    if "place_delay_frames" in samples:
        values["place_delay_frames"] = _median(samples["place_delay_frames"])

    # 4) MCV deploy: game start -> the player's first building is on the map (log must start at frame 0).
    if records and records[0]["frame"] <= 30:
        for r in records:
            pa = r.get("player_index")
            if any(o["owner"] == pa and o["kind"] == "building" and o["on_map"] for o in r.get("objects", [])):
                samples["mcv_deploy_frames"] = [r["frame"]]
                values["mcv_deploy_frames"] = r["frame"]
                notes.append(f"基地車展開：第 {r['frame']} 幀出現建造廠（含你下指令的反應時間）")
                break
    return FitResult(values, samples, notes)
