"""Strategy optimiser CLI.

    .venv\\Scripts\\python.exe ra2opt.py army  scenarios\\soviet_vs_allied.toml
    .venv\\Scripts\\python.exe ra2opt.py build scenarios\\soviet_vs_allied.toml
    .venv\\Scripts\\python.exe ra2opt.py plan  scenarios\\soviet_vs_allied.toml   # army, then its build order
    .venv\\Scripts\\python.exe ra2opt.py pareto scenarios\\soviet_vs_allied.toml --prefer win=2 frames=1
                                  # Pareto front of armies: win margin / ready frame / money spent
    .venv\\Scripts\\python.exe ra2opt.py victory scenarios\\soviet_vs_allied.toml   # 破關: win the game soonest
    .venv\\Scripts\\python.exe ra2opt.py units Russians                          # list unit ids for a country
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import tomllib
from pathlib import Path

from ra2tools.gamedata import COUNTRIES, GameData
from ra2tools.optimize import run_ga
from ra2tools.params import Calibration
from ra2tools.problems import ArmyProblem, BuildOrderProblem, CombatSettings, EnemyScenario

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"


def load_db() -> GameData:
    if not (ROOT / "data" / "rulesmd.ini").exists():
        sys.exit("data/rulesmd.ini not found - run extract.py first")
    return GameData.load(ROOT / "data")


def load_calibration(cfg: dict) -> Calibration:
    """Defaults < calibration/calibration.toml (from calibrate.py fit) < scenario [calibration]."""
    fitted = ROOT / "calibration" / "calibration.toml"
    values = tomllib.loads(fitted.read_text(encoding="utf-8")) if fitted.exists() else {}
    merged = {**values, **cfg.get("calibration", {})}
    if "game_speed" not in merged and merged.get("fps_by_speed"):
        speed = running_game_speed()
        if speed is not None:
            merged["game_speed"] = speed
    cal = Calibration.from_dict(merged)
    if cal.game_speed is not None:
        known = "" if str(cal.game_speed) in cal.fps_by_speed else "（這個速度還沒校準過，用預設 fps）"
        print(f"遊戲速度設定 {cal.game_speed} → {cal.fps:.1f} fps{known}")
    return cal


def running_game_speed() -> int | None:
    """The speed setting of a running gamemd.exe, or None if the game is not running."""
    try:
        from ra2tools.memory import GameMemory, attach
        return GameMemory(attach()).game_speed()
    except Exception:
        return None


def label(db: GameData, tid: str) -> str:
    t = db.technos.get(tid)
    return f"{t.name}({tid})" if t else tid


def ga_kwargs(cfg: dict) -> dict:
    g = cfg.get("ga", {})
    return {k: g[k] for k in ("pop_size", "generations", "seed", "workers", "elite", "tournament") if k in g}


def progress(prefix: str):
    start = time.perf_counter()

    def cb(gen, best, ev):
        state = f"obj={ev.objective:.3f}" if ev.feasible else f"違反={ev.total_violation:.3f} {list(ev.violations)}"
        print(f"\r[{prefix}] 第 {gen:>3} 代  {state}  {time.perf_counter() - start:5.1f}s", end="", flush=True)
    return cb


# ----------------------------------------------------------------------------- army
def optimise_army(db: GameData, cfg: dict) -> tuple[dict[str, int], dict]:
    country = cfg["country"]
    a = cfg.get("army", {})
    cal = load_calibration(cfg)
    exclude = set(a.get("exclude", []))
    candidates = [t.id for t in db.combat_units(country, include_air=a.get("include_air", True))
                  if t.id not in exclude]
    if a.get("only"):
        candidates = [c for c in candidates if c in set(a["only"])]
    enemies = [EnemyScenario(e.get("name", f"enemy{i}"), e["units"], e.get("weight", 1.0))
               for i, e in enumerate(a.get("enemy", []))]
    if not enemies:
        sys.exit("[army] needs at least one [[army.enemy]]")
    problem = ArmyProblem(
        db, country, enemies, candidates, cal, CombatSettings(**cfg.get("combat", {})),
        budget=a.get("budget"), max_units=a.get("max_units"), min_units=a.get("min_units", 1),
        max_per_unit=a.get("max_per_unit", {}), min_per_unit=a.get("min_per_unit", {}),
        min_aa_units=a.get("min_aa_units", 0), min_score=a.get("min_score"),
        deadline_frames=a.get("deadline_frames"), economy_prefix=a.get("economy_prefix", []),
        aggregate=a.get("aggregate", "mean"), cost_weight=a.get("cost_weight", 0.0),
    )
    print(f"候選單位 ({len(candidates)}): {', '.join(label(db, c) for c in candidates)}")
    res = run_ga(problem, on_generation=progress("兵種"), **ga_kwargs(cfg))
    print()
    army = problem.army(res.best)
    ev = res.best_eval
    print(f"\n=== 最佳兵種組合（{'滿足所有約束' if ev.feasible else '未能滿足約束: ' + str(ev.violations)}）===")
    for tid, n in sorted(army.items(), key=lambda kv: -kv[1] * db.technos[kv[0]].cost):
        print(f"  {label(db, tid):<24} x{n:<3} ${db.technos[tid].cost * n}")
    info = ev.info
    print(f"  總價 ${info['cost']}，{info['units']} 個單位，可防空 {info['anti_air']} 個")
    if info.get("finish_frame") is not None:
        print(f"  預設建造順序完成於第 {info['finish_frame']} 幀 (~{info['finish_frame'] / cal.fps:.0f}s @ {cal.fps:g}fps)")
    for name, s in info["scores"].items():
        print(f"  vs {name:<16} score {s:+.2f}  ({'勝' if s > 0 else '敗' if s < 0 else '平'})")
    return army, {"army": army, "feasible": ev.feasible, "violations": ev.violations,
                  "info": info, "history": res.history}


# ---------------------------------------------------------------------------- build
def optimise_build(db: GameData, cfg: dict, targets: dict[str, int] | None = None) -> dict:
    country = cfg["country"]
    b = cfg.get("build", {})
    cal = load_calibration(cfg)
    targets = targets or b.get("targets")
    if not targets:
        sys.exit("[build] needs targets = { ID = count }")
    basics = db.basics(country)
    extras = b.get("extras") or [basics["power"], basics["proc"], basics["factory"], basics["barracks"],
                                 basics["harvester"]]
    problem = BuildOrderProblem(
        db, country, targets, extras, cal, max_frames=b.get("max_frames", 27000),
        auto_power=b.get("auto_power", True), deadline_frames=b.get("deadline_frames"),
        max_count=b.get("max_count", {}), milestones=b.get("milestones", {}),
        max_spent=b.get("max_spent"), max_low_power_frames=b.get("max_low_power_frames"),
        max_extras=b.get("max_extras", 8),
    )
    baseline = problem.evaluate(tuple(problem.target_items))
    res = run_ga(problem, seeds=[tuple(problem.target_items)], on_generation=progress("建造"), **ga_kwargs(cfg))
    print()
    ev = res.best_eval
    r = ev.info["result"]
    print(f"\n=== 最佳建造順序（{'滿足所有約束' if ev.feasible else '未能滿足約束: ' + str(ev.violations)}）===")
    print(f"  目標: {', '.join(f'{label(db, t)} x{n}' for t, n in targets.items())}")
    if r.finish_frame is not None:
        base = baseline.info["result"].finish_frame
        gain = f"，比直接照目標蓋 ({base} 幀) 快 {base - r.finish_frame} 幀" if base else ""
        print(f"  完成時間: 第 {r.finish_frame} 幀 (~{r.finish_frame / cal.fps:.0f}s @ {cal.fps:g}fps){gain}")
    print(f"  花費 ${r.spent:.0f}，採礦收入 ${r.income:.0f}，剩餘 ${r.money:.0f}，"
          f"最低電力餘裕 {r.min_power_balance}")
    print("  時間軸 (完成):")
    for frame, kind, tid in r.events:
        if kind == "done":
            print(f"    {frame:>6} 幀  {label(db, tid)}")
    return {"targets": targets, "sequence": ev.info["sequence"], "feasible": ev.feasible,
            "violations": ev.violations, "finish_frame": r.finish_frame, "spent": r.spent,
            "events": r.events, "history": res.history}


# --------------------------------------------------------------------------- pareto
def optimise_pareto(db: GameData, cfg: dict, prefer: dict[str, float] | None) -> dict:
    """Stage 5: every army not beaten on win margin, ready frame and money at once; a preference
    ([pareto] preference or --prefer) picks one, which execute.py --targets can then build live."""
    from ra2tools.env import BuildEnv
    from ra2tools.live_policy import simulate
    from ra2tools.mission import OBJECTIVES, choose, mission_problem, pareto

    cal = load_calibration(cfg)
    problem = mission_problem(db, cfg, cal, model=str(ROOT / "models" / "build"))
    p = cfg.get("pareto", {})
    prefer = prefer or p.get("preference") or {k: 1.0 for k in OBJECTIVES}
    g = {**cfg.get("ga", {}), **p}
    ga = {k: g[k] for k in ("pop_size", "generations", "seed", "workers") if k in g}
    start = time.perf_counter()
    res = pareto(problem, on_generation=lambda gen, front: print(
        f"\r[Pareto] 第 {gen:>3} 代  前緣 {len(front)} 個  {time.perf_counter() - start:5.0f}s", end="", flush=True), **ga)
    print(f"\n\n=== Pareto 前緣：{len(res.front)} 種兵種組合（沒有一種在勝率、時間、花費上同時被別的組合打敗）===")
    print(f"  {'#':>2} {'勝差':>6} {'完成幀':>7} {'花費':>7}  兵種")
    pick = choose(res, prefer)
    front = []
    for i, (gnm, ev) in enumerate(zip(res.front, res.evals)):
        army = problem.army(gnm)
        m, f, s = ev.objectives
        mark = "←" if i == pick else " "
        print(f"{mark} {i:>2} {-m:>+6.2f} {f:>7.0f} {s:>7.0f}  " + ", ".join(f"{label(db, t)}x{n}" for t, n in army.items())
              + ("" if ev.feasible else f"  未滿足 {list(ev.violations)}"))
        front.append({"army": army, "win": -m, "frames": f, "spent": s, "scores": ev.info["scores"],
                      "feasible": ev.feasible})
    chosen = front[pick]
    print(f"\n偏好 {prefer} → 第 {pick} 個：勝差 {chosen['win']:+.2f}，第 {chosen['frames']:.0f} 幀完成，花費 ${chosen['spent']:.0f}")
    for name, sc in chosen["scores"].items():
        print(f"  vs {name:<16} score {sc:+.2f}  ({'勝' if sc > 0 else '敗' if sc < 0 else '平'})")
    order = []
    planner = problem.planner
    if planner is not None and planner._policy is not None:
        env = BuildEnv(db, cfg["country"], chosen["army"], cal, vocab=planner._policy.vocab)
        events, *_ = simulate(env, planner._policy, {"frames": 1.0, "spent": 0.0})
        order = [t for _, k, t in events if k == "start"]
        print(f"建造順序（訓練好的模型）：{' → '.join(order)}")
    print("實機執行：execute.py --targets " + " ".join(f"{t}={n}" for t, n in chosen["army"].items()))
    return {"preference": prefer, "front": front, "chosen": pick, "order": order, "history": res.history}


# --------------------------------------------------------------------------- victory
def optimise_victory(db: GameData, cfg: dict, prefer: dict[str, float] | None) -> dict:
    """Stage 6 (破關): armies that win the game soonest, cheapest and with the most left, against
    AIs whose strength over time was measured live ([victory] opponents, from opponent.py extract).
    With several opponents (easy / medium / hard) each one's victory frame is its own objective,
    and the preference says which difficulty matters (e.g. --prefer hard=1 spent=0.3)."""
    import math

    from ra2tools.mission import choose_victory, pareto, victory_objectives, victory_problem, victory_seeds
    from ra2tools.opponent import Opponent

    v = cfg.get("victory", {})
    paths = v.get("opponents") or [v.get("opponent", "calibration/opponents/easy_americans_firestorm.json")]
    opps = [Opponent.load(ROOT / p) for p in paths]
    cal = load_calibration(cfg)
    problem = victory_problem(db, cfg, cal, opps, model=str(ROOT / "models" / "build"))
    names = victory_objectives(problem)
    prefer = prefer or v.get("preference") or {"victory": 1.0, "spent": 0.3, "survive": 0.3}
    ga = {k: v[k] for k in ("pop_size", "generations", "seed", "workers") if k in v}
    for o in opps:
        q = (o.notes or {}).get("quantile")
        print(f"對手 {o.name}（AI {o.country}，難度 {o.difficulty or '?'}，基地相距 {o.base_distance:.0f} 格"
              + (f"，{q['games']} 場的第 {q['p']:.0%} 分位" if q else "") + "）")
    print(f"我方開局部隊 {opps[0].player_start_army}")
    # TopK seeds (paper): known armies from the scenario and from earlier search fronts.
    known = list(v.get("seeds", []))
    patterns = v.get("seed_results", [])
    for pattern in [patterns] if isinstance(patterns, str) else patterns:
        for path in sorted(glob.glob(str(ROOT / pattern))):
            known += [{**r["army"], **({"early": r["early"]} if r.get("early") else {})}
                      for r in json.loads(Path(path).read_text(encoding="utf-8"))["front"] if r.get("feasible")]
    seeds = victory_seeds(problem, known, v.get("top_k", ga.get("pop_size", 60) // 4)) if known else []
    if seeds:
        print(f"初始族群：{len(known)} 個已知方案中挑最好的 {len(seeds)} 個，其餘隨機")
    start = time.perf_counter()
    res = pareto(problem, seeds=seeds, on_generation=lambda gen, front: print(
        f"\r[破關] 第 {gen:>3} 代  前緣 {len(front)} 個  {time.perf_counter() - start:5.0f}s", end="", flush=True), **ga)
    pick = choose_victory(problem, res, prefer) if res.evals else 0
    k = len(opps)
    print(f"\n\n=== 破關 Pareto 前緣：{len(res.front)} 種部隊（獲勝幀，'-' = 打不贏）===")
    print(f"  {'#':>2} " + " ".join(f"{n[:8]:>8}" for n in names[:k])
          + f" {'花費':>7} {'剩下':>5} {'蓋好':>6}  部隊（不含開局部隊）")
    rows = []
    for i, (g, ev) in enumerate(zip(res.front, res.evals)):
        info = ev.info
        army = problem.army(g)
        early = problem.early_of(g)
        wins = {o.name: info["vs"][o.name]["victory_frame"] for o in opps}
        cells = " ".join(f"{wins[o.name]:>8.0f}" if wins[o.name] else f"{'-':>8}" for o in opps)
        print(f"{'←' if i == pick else ' '} {i:>2} {cells} {ev.objectives[k]:>7.0f} {-ev.objectives[k + 1]:>5.0%} "
              f"{info['finish_frame']:>6.0f}  "
              + (("先蓋 " + ", ".join(f"{label(db, t)}x{n}" for t, n in early.items()) + " → ") if early else "")
              + (", ".join(f"{label(db, t)}x{n}" for t, n in army.items()) or "（只用開局部隊）")
              + ("" if ev.feasible else f"  未滿足 {list(ev.violations)}"))
        rows.append({"army": army, "early": early, "victory": wins, "spent": ev.objectives[k], "survive": -ev.objectives[k + 1],
                     "ready": info["finish_frame"], "feasible": ev.feasible,
                     "enemy": {o.name: info["vs"][o.name].get("enemy") for o in opps}})
    if not rows or not rows[pick]["feasible"]:
        print("\n找不到打得贏的部隊（試著放寬 max_units 或增加代數）")
        return {"opponents": [o.name for o in opps], "front": rows}
    c = rows[pick]
    print(f"\n偏好 {prefer}（目標順序 {names}）→ 第 {pick} 個，花費 ${c['spent']:.0f}")
    for o, n in zip(opps, names):
        w = c["victory"][o.name]
        print(f"  vs {n:<8} " + (f"第 {w:.0f} 幀獲勝（約 {w / cal.fps / 60:.1f} 分鐘）"
                                                     if w and math.isfinite(w) else "打不贏"))
    if c["early"]:
        print("先蓋（防守組）：" + " ".join(f"{t}={n}" for t, n in c["early"].items()))
    print("實機執行：execute.py --attack --targets " + " ".join(f"{t}={n}" for t, n in c["army"].items())
          + (" --early " + " ".join(f"{t}={n}" for t, n in c["early"].items()) if c["early"] else ""))
    return {"opponents": [o.name for o in opps], "preference": prefer, "front": rows, "chosen": pick,
            "history": res.history}


def save(name: str, data: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n結果已存到 {path}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="RA2:YR strategy optimiser")
    ap.add_argument("mode", choices=["army", "build", "plan", "pareto", "victory", "units"])
    ap.add_argument("target", help="scenario .toml (or country name for 'units')")
    ap.add_argument("--prefer", nargs="+", help="pareto/victory：偏好權重，例如 win=2 frames=1 spent=0 或 victory=1 spent=0.3")
    args = ap.parse_args()
    db = load_db()

    if args.mode == "units":
        if args.target not in COUNTRIES:
            sys.exit(f"country must be one of: {', '.join(COUNTRIES)}")
        for t in db.combat_units(args.target):
            aa = "防空" if any(w.aa for w in t.combat_weapons) else ""
            kind = "air" if t.is_air else t.kind
            print(f"{t.id:<10} {t.name:<10} ${t.cost:<5} HP {t.strength:<5} {kind:<8} {aa}")
        return

    cfg = tomllib.loads(Path(args.target).read_text(encoding="utf-8"))
    if cfg.get("country") not in COUNTRIES:
        sys.exit(f"country must be one of: {', '.join(COUNTRIES)}")
    name = Path(args.target).stem
    if args.mode == "army":
        _, out = optimise_army(db, cfg)
        save(f"{name}_army", out)
    elif args.mode == "pareto":
        prefer = {k: float(v) for k, _, v in (x.partition("=") for x in args.prefer)} if args.prefer else None
        save(f"{name}_pareto", optimise_pareto(db, cfg, prefer))
    elif args.mode == "victory":
        prefer = {k: float(v) for k, _, v in (x.partition("=") for x in args.prefer)} if args.prefer else None
        save(f"{name}_victory", optimise_victory(db, cfg, prefer))
    elif args.mode == "build":
        save(f"{name}_build", optimise_build(db, cfg))
    else:
        army, army_out = optimise_army(db, cfg)
        print()
        build_out = optimise_build(db, cfg, targets=army)
        save(f"{name}_plan", {"army": army_out, "build": build_out})


if __name__ == "__main__":
    main()
