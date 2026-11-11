"""Run an optimised build order in a live skirmish through ra2bridge.dll, then compare with the sim.

    .venv\\Scripts\\python.exe execute.py results\\soviet_vs_allied_build_....json
    .venv\\Scripts\\python.exe execute.py --order NAPOWR NAREFN NAWEAP HTNK HTNK
    .venv\\Scripts\\python.exe execute.py --targets HTNK=3 --weight 0.75   # trained policy decides live

Start the game first (autostart.py start). The predicted timeline is re-simulated with the
current calibration, aligned to the frame when the MCV was deployed. With --targets there is no
fixed order: the trained policy (models/build) is asked whenever a production queue is empty,
and the prediction is the same policy played in the simulator.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import tomllib
from collections import Counter
from dataclasses import replace
from pathlib import Path

from ra2opt import label, load_calibration, load_db
from ra2tools import launcher
from ra2tools.bridge import Bridge, BridgeError
from ra2tools.economy import EconomySim
from ra2tools.executor import BuildExecutor, compare
from ra2tools.memory import GameMemory, GameNotFound, attach

ROOT = Path(__file__).parent


def load_order(args) -> tuple[list[str], dict[str, int]]:
    if args.order:
        order = [t.upper() for t in args.order]
        return order, dict(Counter(order))
    data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    data = data.get("build", data)  # plan results nest the build order
    return data["sequence"], data["targets"]


def measured_delays(db, events, deploy_frame: int) -> dict:
    """bot_place_delay_frames (median of done - start - build time over buildings) and
    bot_mcv_deploy_frames (deploy order -> first production order, an upper bound)."""
    out = {}
    starts: dict[str, list[int]] = {}
    delays = []
    for frame, kind, tid in events:
        if kind == "start":
            starts.setdefault(tid, []).append(frame)
        elif kind == "done" and db.technos[tid].kind == "building" and starts.get(tid):
            delays.append(frame - starts[tid].pop(0) - db.build_frames(db.technos[tid]))
    if delays:
        out["bot_place_delay_frames"] = sorted(delays)[len(delays) // 2]
    first = next((f for f, k, _ in events if k == "start"), None)
    if first is not None and any(k == "deploy" for _, k, _ in events):
        out["bot_mcv_deploy_frames"] = first - deploy_frame
    return out


def report(db, predicted_events, ex, offset: int, pred_finish) -> list[dict]:
    rows = compare(predicted_events, ex.events, offset)
    print(f"\n{'項目':<14}{'預測開始':>8}{'實際開始':>8}{'預測完成':>8}{'實際完成':>8}{'誤差':>7}")
    for r in rows:
        fmt = lambda v: "-" if v is None else str(v)
        print(f"{r['type'] + ' #' + str(r['n']):<14}{r['pred_start']:>10}{fmt(r['start']):>10}"
              f"{r['pred_done']:>10}{fmt(r['done']):>10}{fmt(r['diff']):>8}")
    last = max((f for f, k, _ in ex.events if k == "done"), default=None)
    if ex.done and last is not None and pred_finish:
        last -= offset
        err = (last - pred_finish) / pred_finish
        print(f"\n全部完成：實際第 {last} 幀，預測第 {pred_finish:.0f} 幀（誤差 {err:+.1%}，"
              f"已扣掉開始前的 {offset} 幀）")
    else:
        print("\n逾時，未完成全部項目")
    return rows


def predict_victory(args, db, country, cal, targets, policy, early: dict | None = None) -> dict:
    """VictoryProblem's view of this army: ready, arrival and victory frames against the opponent."""
    from ra2tools.mission import PolicyPlanner
    from ra2tools.opponent import Opponent
    from ra2tools.problems import CombatSettings, VictoryProblem

    opp = Opponent.load(Path(args.opponent))
    cands = list(targets)
    early = early or {}
    p = VictoryProblem(db, country, [], cands, cal, CombatSettings(**args.cfg.get("combat", {})),
                       planner=PolicyPlanner(db, country, cal, args.model), opponent=opp, early=list(early),
                       garrison=args.cfg.get("victory", {}).get("garrison", 0))
    ev = p.evaluate(p.genome_of(targets, early))
    i = ev.info
    out = {"ready": i["finish_frame"], "depart": i.get("depart_frame"), "arrive": i["arrive_frame"],
           "battle": i.get("battle_frames"),
           "victory": i.get("victory_frame"), "army": i["army"], "enemy": i.get("enemy"),
           "a_left": i.get("a_left"), "b_left": i.get("b_left"), "start_army": opp.player_start_army,
           "guard": i.get("guard"), "base_lost": (i.get("vs") or {}).get(opp.name, {}).get("base_lost")}
    win = f"第 {out['victory']:.0f} 幀獲勝" if out["victory"] else f"打不贏（敵方剩 {out['b_left']:.0%}）"
    wait = (f"，先在家擋反攻到第 {out['depart']:.0f} 幀才出發"
            if out["depart"] and out["depart"] > out["ready"] + 1 else "")
    print(f"破關預測（對手 {opp.name}）：第 {out['ready']:.0f} 幀蓋好{wait}，第 {out['arrive']:.0f} 幀抵達，{win}")
    print(f"  抵達時敵方：{out['enemy']}")
    return out


_camera_at = {"frame": -10**9}
RECALL_SHARE = 0.25  # an AI wave worth this share of our army calls it back while gathering
WAVE_LOST = 0.15  # the attacking army down to this share of its value: the wave is lost, rebuild
NEXT_WAVE = 0.7   # the next wave leaves at this share of the first one's value (or once broke at 0.3)
REGROUP_MAX = 6000  # frames: then it leaves at 0.3 too (the plan's tech may be gone, so the rest never comes)
# The AI's Chronosphere drops 4-6 tanks next to our base 10-15k frames after it stands (median
# 13000, series_v4-v7): from CHRONO_ALERT after first seeing it, the reinforcements are bought to
# beat such a drop and stay home instead of joining the next wave.
CHRONOSPHERES = {"GACSPH"}
CHRONO_ALERT = 7000  # seen only once the attack phase runs: it may have stood a while
CHRONO_DROP = {"MTNK": 4, "SREF": 1, "MGTK": 1}
ADAPT_EVERY = 3000  # frames between counter checks while building
ADAPT_UNITS = ["FLAKT", "HTK", "HTNK", "E2", "TTNK", "APOC", "V3", "DRON"]  # always weighed as counters


def follow_camera(bridge, a, snap, every: int = 15) -> None:
    """Keep our view on the army's main body (for recording videos); display only."""
    if snap.frame - _camera_at["frame"] < every:
        return
    army = a.army(snap)
    if not army:
        return
    med = lambda v: sorted(v)[len(v) // 2]
    try:
        bridge.camera((med([o.x for o in army]), med([o.y for o in army])))
    except BridgeError:
        return
    _camera_at["frame"] = snap.frame


def run_attack(args, db, gm, bridge, ex, end, hold: int = 0, guard: frozenset = frozenset(),
               steward=None, reinforcer=None, rebuilder=None) -> dict:
    """After the build: every combat unit (the free starting ones too) attacks until the AI is out.

    Like VictoryProblem.fight, the army first stays home for `hold` frames when an AI wave is due
    before it would arrive, and fights any wave that reaches the base before the attack starts:
    under ShortGame an empty base razed by the wave loses the match. With a `rebuilder`, a lost
    wave sends the survivors home, the bank buys the plan's units again and the next wave goes."""
    from ra2tools.assault import Assault, Defence

    snap = gm.snapshot()
    enemy = next(h.index for h in snap.houses if h.index != snap.player_index
                 and h.country not in ("Neutral", "Special") and not h.defeated)
    a = Assault(db, bridge, enemy, exclude=guard)
    defence = Defence(db, bridge, enemy)  # everyone defends until the attack starts
    home = Defence(db, bridge, enemy, only=guard)  # then the guard alone (and the reinforcements)
    claimed = lambda: steward.claimed if steward else set()
    kept = lambda: reinforcer.units if reinforcer else set()
    hold_until = snap.frame + hold
    if guard:
        print(f"\n留守部隊：{', '.join(sorted(guard))}（開局送的步兵，方案裡沒有用到）")
    if hold > 0:
        print(f"\n第 {snap.frame} 幀蓋好，預計有 AI 反攻，在家防守到第 {hold_until} 幀")
    army = Counter(o.type_id for o in a.army(snap))
    print(f"\n第 {snap.frame} 幀開始進攻：{dict(army)}")
    log = {"start": snap.frame, "army": dict(army), "timeline": [], "waves": []}
    last_buildings = None
    value = lambda units: sum(db.technos[o.type_id].cost or 1 for o in units)
    first_value = wave_value = None  # army value as the first / the current wave attacked
    regroup = False
    chrono_seen = None  # frame we first saw the AI's Chronosphere
    while time.monotonic() < end:
        snap = gm.snapshot()
        if not snap.houses:  # the match is over (score screen or exit): no house data left
            print(f"  {snap.frame:>6} 幀  對戰已結束")
            break
        if snap.player and snap.player.defeated:
            print(f"  {snap.frame:>6} 幀  我方被擊敗")
            log["defeated"] = snap.frame
            break
        if a.defeated(snap):
            log["victory"] = snap.frame
            break
        a.skip = defence.skip = claimed() | kept()
        home.skip, home.extra = claimed(), kept()
        if a.attack_started is not None:
            # Raiders at home (paratroopers on the buildings) are fought by everything still near
            # it, units on their way to the front included: live 2026-11-07 (game 8) the tanks
            # kept home sat next to the paratroopers and Boris walked past them.
            centre = home._home(snap)
            if centre is not None and home.threats(snap):
                near = {o.address for o in a.army(snap) if math.dist((o.x, o.y), centre) <= home.radius}
                home.extra = home.extra | near
                a.skip = a.skip | near
        if chrono_seen is None and any(o.type_id in CHRONOSPHERES for o in a.enemies(snap)):
            chrono_seen = snap.frame
            print(f"  {snap.frame:>6} 幀  看到 AI 的超時空傳送儀，第 {snap.frame + CHRONO_ALERT} 幀起留兵守家")
            log["chrono_seen"] = snap.frame
        alert = chrono_seen is not None and snap.frame >= chrono_seen + CHRONO_ALERT
        raid = home.threats(snap) if a.attack_started is not None or regroup else []
        if reinforcer:
            reinforcer.home_threat = CHRONO_DROP if alert else {}
            # Raiders at the base while the army is away or rebuilding: counters to them first,
            # every few seconds, from whatever is home (series_v13 game 3: the base fell with $12k).
            centre = home._home(snap)
            reinforcer.raid = dict(Counter(t.type_id for t in raid))
            reinforcer.defenders = dict(Counter(o.type_id for o in defence.army(snap) if centre is not None
                                                and math.dist((o.x, o.y), centre) <= home.radius))
        if steward:
            steward.tick(snap)
        if reinforcer and (a.attack_started is not None or (regroup and raid)):
            reinforcer.tick(snap)
        threats = defence.threats(snap) if a.attack_started is None else []
        # Only what the army can hurt counts, and from the first tick on: live 2026-11-07
        # (game 13) a wave at the base as the build finished left a.started unset, so two GIs and
        # a Harrier the Apocalypses cannot shoot then held the whole army home for 5000 frames.
        army_now = defence.army(snap)
        threats = [t for t in threats if any(a._dps(u, t) > 0 for u in army_now)]
        if threats:
            # Once on the way, only a real wave brings the army back; the guard and the reinforcements
            # take raiders. Live 2026-11-06 single raiders kept recalling it, the gather timer ran
            # out meanwhile and the ground went in without the Kirovs (games 4-7).
            threats = threats if value(threats) >= RECALL_SHARE * value(a.army(snap)) else []
        if (rebuilder and wave_value and a.attack_started is not None
                and value(a.army(snap)) < WAVE_LOST * wave_value):
            # Live 2026-11-07 (series_v7 games 1-3) the army died at the AI base and the game sat
            # out the timeout on $30-40k. A fresh Assault walks the survivors home and gathers again.
            print(f"  {snap.frame:>6} 幀  這一波打輸了（剩 {dict(Counter(o.type_id for o in a.army(snap)))}），"
                  f"回家用 ${snap.player.credits} 重新組軍")
            log["waves"].append({"lost": snap.frame, "credits": snap.player.credits})
            orders, order_log, move_log = a.orders, a.order_log, a.move_log
            a = Assault(db, bridge, enemy, exclude=guard)
            a.orders, a.order_log, a.move_log = orders, order_log, move_log
            a.skip = claimed()
            if reinforcer and not alert:
                reinforcer.units.clear()  # what it kept home joins the next wave (unless a drop is due)
            regroup, wave_value = True, None
        if regroup:
            ready = a.army(snap)
            have = Counter(o.type_id for o in ready)
            waited = snap.frame - log["waves"][-1]["lost"] > REGROUP_MAX
            if value(ready) >= NEXT_WAVE * first_value or ((rebuilder.broke(snap) or waited)
                                                           and value(ready) >= 0.3 * first_value):
                regroup = False
                print(f"  {snap.frame:>6} 幀  第 {len(log['waves']) + 1} 波組好：{dict(have)}，${snap.player.credits}")
                log["waves"][-1]["ready"] = snap.frame
            elif not (reinforcer and raid):  # the factories are the Reinforcer's while raided
                if reinforcer and not alert:
                    reinforcer.units.clear()  # its raid defenders join the next wave
                rebuilder.tick(snap, have)
        if a.attack_started is None and (snap.frame < hold_until or threats or regroup):
            defence.tick(snap)  # also keeps the yard guard and walks strays home
            if a.stage_started is not None:  # gathering starts over once the wave is beaten
                a.stage_started = snap.frame
                a._ordered_at.clear()
            time.sleep(args.interval)
            continue
        home.tick(snap)  # the guard, the reinforcements kept home and whatever is near a raid
        gathering = a.attack_started is None
        a.tick(snap)
        if args.camera:
            follow_camera(bridge, a, snap)
        if gathering and a.attack_started is not None:
            print(f"  {snap.frame:>6} 幀  集結完成（集結點 {a.rally}），{len(a.army(snap))} 個單位一起進攻")
            if a.opening:
                o = a.opening
                why = "兵力夠了" if o["reason"] == "ratio" else "等太久"
                print(f"  {snap.frame:>6} 幀  出手時機：我方 ${o['ours']:.0f} / AI 留守 ${o['ai_home']:.0f}"
                      f" = {o['ratio']}，等了 {o['waited']} 幀（{why}）")
            log.setdefault("gathered", snap.frame)
            log.setdefault("openings", []).append(a.opening)
            wave_value = value(a.army(snap))
            first_value = first_value or wave_value
        nb = sum(1 for o in a.enemies(snap) if o.kind == "building")
        if nb != last_buildings:
            mine = len(a.army(snap))
            print(f"  {snap.frame:>6} 幀  敵方建築 {nb}，我方部隊 {mine}")
            log["timeline"].append((snap.frame, nb, mine))
            last_buildings = nb
        time.sleep(args.interval)
    snap = gm.snapshot()
    log["survivors"] = dict(Counter(o.type_id for o in a.army(snap)))
    log["orders"] = a.orders
    log["order_log"] = a.order_log
    # Every MOVE and its bridge result (series_v11 game 1: vehicles ignored the gather for 3800
    # frames with no trace of why); errors also summed up for a quick look.
    # who gave the order: the assault, the all-army defence before it, or the home guard after
    moves = sorted([(*m, "assault") for m in a.move_log] + [(*m, "defence") for m in defence.move_log]
                   + [(*m, "home") for m in home.move_log])
    log["move_log"] = moves
    errors = Counter(m[4] for m in moves if m[4] != "ok")
    log["move_errors"] = dict(errors)
    if errors:
        print(f"移動指令失敗：{dict(errors)}（共 {len(moves)} 次移動）")
    return log


def report_attack(pred, log, offset: int) -> None:
    if log is None:
        print("沒有蓋完，沒有進攻")
        return
    won = log.get("victory")
    print(f"\n進攻：第 {log['start'] - offset} 幀出發（預測 {pred['ready']:.0f}），"
          + (f"第 {won - offset} 幀獲勝" if won else f"第 {log['defeated'] - offset} 幀我方被擊敗"
             if log.get("defeated") else "逾時仍未獲勝")
          + (f"（預測 {pred['victory']:.0f}，誤差 {(won - offset - pred['victory']) / pred['victory']:+.1%}）"
             if won and pred.get("victory") else "")
          + f"；下了 {log['orders']} 次攻擊指令，剩下 {log['survivors']}")


def run_policy(args, cfg, db) -> None:
    """--targets: the trained policy chooses every item live."""
    from ra2tools.env import BuildEnv
    from ra2tools.live_policy import PolicyExecutor, simulate
    from ra2tools.policy import BuildPolicy

    def parse(items):
        out = {}
        for item in items:
            tid, _, n = item.partition("=")
            out[tid.upper()] = int(n or 1)
        return out
    targets, early = parse(args.targets), parse(args.early)
    total = dict(Counter(targets) + Counter(early))
    weights = {"frames": args.weight, "spent": 1 - args.weight}
    policy = BuildPolicy(Path(args.model))
    pids = launcher.process_ids()
    if not pids:
        sys.exit("遊戲沒有在執行（可以先用 autostart.py start）")
    try:
        gm = GameMemory(attach())
        snap = gm.snapshot()
        country = snap.player.country if snap.player else cfg["country"]
        cal = replace(load_calibration(cfg).as_bot(), start_credits=snap.player.credits)
        sim_env = BuildEnv(db, country, total, cal, vocab=policy.vocab)
        predicted, pred_finish, pred_spent, pred_ok = simulate(sim_env, policy, weights, early)
        plan = [t for _, k, t in predicted if k == "start"]
        print(f"國家 {country}，目標 {targets}" + (f"，先蓋 {early}" if early else "") + f"，權重 時間 {weights['frames']:.2f} / 花費 {weights['spent']:.2f}")
        print(f"模擬（同一個模型）：{' → '.join(plan)}")
        print(f"  預測第 {pred_finish:.0f} 幀{'完成' if pred_ok else '仍未完成'}，花費 ${pred_spent:.0f}")
        victory = predict_victory(args, db, country, cal, targets, policy, early) if args.attack else None

        from ra2tools.live_policy import first_stage
        env = BuildEnv(db, country, first_stage(db, country, early) if early else total, cal, vocab=policy.vocab)
        with Bridge.connect(pids[0]) as bridge:
            from ra2tools.placement import Spreader
            spreader = Spreader(db)
            ex = PolicyExecutor(db, [], bridge, env=env, policy=policy, weights=weights,
                                then=total if early else None, placer=spreader)
            from ra2tools.adapt import Reinforcer, counter_pick, enemy_army
            from ra2tools.mapdata import load_fields
            from ra2tools.problems import CombatSettings
            from ra2tools.steward import Steward
            vcfg = cfg.get("victory", {})
            steward = Steward(db, bridge, country, fields=load_fields(),
                              garrison=vcfg.get("garrison", 0) if args.attack or args.defend else 0, placer=spreader)
            combat = CombatSettings(**{**cfg.get("combat", {}), **vcfg.get("combat", {})})
            counters = sorted({t for t in [*targets, *early, *ADAPT_UNITS] if t in policy.vocab
                               and db.technos[t].kind != "building" and db.technos[t].combat_weapons})
            adapted, adapt_spent, adapt_at = [], 0.0, 0
            reinforcer = None
            start_army = (victory or {}).get("start_army") or {}
            end = time.monotonic() + args.timeout
            reported = 0
            defence = None
            if args.attack or args.defend:  # the AI may attack before the army is ready
                from ra2tools.assault import Defence
                enemy = next(h.index for h in snap.houses if h.index != snap.player_index
                             and h.country not in ("Neutral", "Special"))
                defence = Defence(db, bridge, enemy)
                defence.skip = steward.claimed
            ex.hidden = steward.inside  # else the build policy rebuilds the garrisoned conscripts
            under_attack = False
            while not ex.done and time.monotonic() < end:
                snap = gm.snapshot()
                ex.tick(snap)
                steward.tick(snap)
                if args.adapt and defence is not None and snap.frame - adapt_at >= ADAPT_EVERY \
                        and adapt_spent < args.adapt:
                    # What the AI fields now vs the army we are building: add counters while it loses.
                    adapt_at = snap.frame
                    seen = enemy_army(db, snap, defence.enemy)
                    stage = ex.then if ex.then is not None else ex.env.targets
                    planned = dict(Counter(stage) + Counter(start_army))
                    add = counter_pick(db, cal, combat, planned, seen, counters, args.adapt - adapt_spent)
                    if add:
                        for t, n in add.items():
                            stage[t] = stage.get(t, 0) + n
                        adapt_spent += sum(db.technos[t].cost * n for t, n in add.items())
                        adapted.append((snap.frame, seen, add))
                        print(f"  {snap.frame:>6} 幀  看到 AI 部隊 {seen}，追加 {add}（累計 ${adapt_spent:.0f}）")
                if defence is not None:
                    threats = defence.threats(snap)
                    if bool(threats) != under_attack:
                        under_attack = bool(threats)
                        print(f"  {snap.frame:>6} 幀  " + (f"AI 進攻基地（{len(threats)} 個單位），全軍防守"
                                                         if under_attack else "擊退進攻"))
                    defence.tick(snap)
                if snap.player and snap.player.defeated:
                    print(f"  {snap.frame:>6} 幀  我方被擊敗")
                    break
                for frame, kind, tid in ex.events[reported:]:
                    credits = gm.snapshot(with_objects=False).player.credits
                    print(f"  {frame:>6} 幀  {kind:<6} {label(db, tid)}  ${credits}")
                reported = len(ex.events)
                time.sleep(args.interval)
            if args.attack and ex.done:
                hold = 0
                if victory and victory.get("depart"):
                    hold = max(0, round(victory["depart"] - victory["ready"]))
                from ra2tools.problems import home_guard
                guard = frozenset(home_guard(db, Counter(total), victory.get("start_army") or {}))
                if args.reinforce:
                    reinforcer = Reinforcer(db, bridge, cal, combat, defence.enemy, counters,
                                            home=Counter(home_guard(db, Counter(total), start_army)))
                steward.rebuild = True  # the build is done: the production queues are free
                from ra2tools.adapt import Rebuilder
                rebuilder = Rebuilder(db, bridge, targets, factory=db.basics(country).get("factory"),
                                      placer=spreader) if args.waves else None
                attack_log = run_attack(args, db, gm, bridge, ex, end, hold, guard, steward, reinforcer, rebuilder)
            elif args.defend:  # observation game: stay home until the timeout, the AI plays on
                print(f"  {snap.frame:>6} 幀  蓋完，只守家（觀察局）")
                steward.rebuild = True
                while time.monotonic() < end:
                    snap = gm.snapshot()
                    if snap.player and snap.player.defeated:
                        print(f"  {snap.frame:>6} 幀  我方被擊敗")
                        break
                    threats = defence.threats(snap)
                    if bool(threats) != under_attack:
                        under_attack = bool(threats)
                        print(f"  {snap.frame:>6} 幀  " + (f"AI 進攻基地（{len(threats)} 個單位），全軍防守"
                                                         if under_attack else "擊退進攻"))
                    defence.tick(snap)
                    steward.tick(snap)
                    time.sleep(args.interval)
    except (BridgeError, GameNotFound) as e:
        sys.exit(f"失敗：{e}")

    offset = ex.deploy_frame or 0
    rows = report(db, predicted, ex, offset, pred_finish if pred_ok else None)
    if args.attack:
        report_attack(victory, attack_log if ex.done else None, offset)
    actual_plan = [t for _, k, t in ex.events if k == "start"]
    same = "相同" if actual_plan == plan else "不同"
    print(f"實際順序（與模擬{same}）：{' → '.join(actual_plan)}")
    ms = [d["ms"] for d in ex.decisions]
    if ms:
        print(f"模型決策 {len(ms)} 次（其中等待 {sum(d['choice'] == 'WAIT' for d in ex.decisions)} 次），"
              f"平均 {sum(ms) / len(ms):.2f} ms，最慢 {max(ms):.2f} ms；花費 ${ex.spent:.0f}；"
              f"安全網介入 {policy.unstuck} 次")
    log = Path(args.log) if args.log else ROOT / "logs" / f"policy_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log.parent.mkdir(exist_ok=True)
    log.write_text(json.dumps({"country": country, "targets": targets, "weights": weights,
                               "deploy_frame": offset, "predicted": predicted, "pred_finish": pred_finish,
                               "actual": ex.events, "decisions": ex.decisions, "rows": rows,
                               "finished": ex.done, "spent": ex.spent,
                               "victory_predicted": victory, "attack": attack_log if args.attack and ex.done else None,
                               "steward": steward.log, "adapted": adapted,
                               "reinforced": reinforcer.log if reinforcer else []},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"記錄已存到 {log}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # a killed run keeps its log
    ap = argparse.ArgumentParser(description="Execute a build order in a live game")
    ap.add_argument("result", nargs="?", help="ra2opt build/plan 結果 JSON")
    ap.add_argument("--order", nargs="+", help="直接指定建造順序（種類 ID）")
    ap.add_argument("--targets", nargs="+", help="由訓練好的模型即時決策，例如 HTNK=3 SHK=2")
    ap.add_argument("--weight", type=float, default=1.0, help="--targets：時間的權重（其餘是花費）")
    ap.add_argument("--model", default=str(ROOT / "models" / "build"))
    ap.add_argument("--early", nargs="+", default=[],
                    help="--targets：先蓋的防守組（ra2opt victory 的 early），例如 HTK=8 TESLA=2；單位也算進部隊")
    ap.add_argument("--attack", action="store_true", help="--targets：蓋完後全軍進攻，直到擊敗 AI（破關）")
    ap.add_argument("--defend", action="store_true", help="--targets：蓋完後只守家到 --timeout（觀察局，給 opponent.py quantile）")
    ap.add_argument("--camera", action="store_true", help="--attack：鏡頭跟著部隊（錄影用）")
    ap.add_argument("--adapt", type=float, default=5000,
                    help="--targets：依看到的 AI 部隊追加剋制兵種，最多花這麼多（0 = 不追加）")
    ap.add_argument("--no-reinforce", dest="reinforce", action="store_false",
                    help="--attack：出發後用多的錢蓋剋制兵種留在家守（這個選項關掉）")
    ap.add_argument("--no-waves", dest="waves", action="store_false",
                    help="--attack：這一波打輸後回家用存款照方案重新組軍、再打下一波（這個選項關掉）")
    ap.add_argument("--opponent", default=str(ROOT / "calibration" / "opponents" / "easy_americans_firestorm.json"),
                    help="--attack 的預測用：AI 隨時間的部隊與建築（opponent.py extract）")
    ap.add_argument("--scenario", default=str(ROOT / "scenarios" / "soviet_vs_allied.toml"))
    ap.add_argument("--timeout", type=float, default=900, help="最多執行幾秒")
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--log", help="結果 JSON（預設 logs/execute_<時間>.json）")
    args = ap.parse_args()
    if not args.result and not args.order and not args.targets:
        ap.error("需要結果 JSON、--order 或 --targets")

    cfg = tomllib.loads(Path(args.scenario).read_text(encoding="utf-8"))
    args.cfg = cfg
    db = load_db()
    if args.targets:
        run_policy(args, cfg, db)
        return
    order, targets = load_order(args)

    pids = launcher.process_ids()
    if not pids:
        sys.exit("遊戲沒有在執行（可以先用 autostart.py start）")
    try:
        gm = GameMemory(attach())
        snap = gm.snapshot()
        country = snap.player.country if snap.player else cfg["country"]
        # Bot timings for placement/deploy, and 1-frame steps so the prediction is exact.
        sim = EconomySim(db, country, load_calibration(cfg).as_bot(), dt=1)
        seq = sim.resolve(order, targets)
        predicted = sim.run(seq, targets, resolved=True)
        print(f"國家 {country}，建造順序：{' → '.join(seq)}")
        print(f"模擬預測：第 {predicted.finish_frame} 幀完成（從第 0 幀起算）")

        with Bridge.connect(pids[0]) as bridge:
            ex = BuildExecutor(db, seq, bridge)
            end = time.monotonic() + args.timeout
            reported = 0
            while not ex.done and time.monotonic() < end:
                ex.tick(gm.snapshot())
                for frame, kind, tid in ex.events[reported:]:
                    credits = gm.snapshot(with_objects=False).player.credits
                    print(f"  {frame:>6} 幀  {kind:<6} {label(db, tid)}  ${credits}")
                reported = len(ex.events)
                time.sleep(args.interval)
    except (BridgeError, GameNotFound) as e:
        sys.exit(f"失敗：{e}")

    # The sim's frame 0 is game start with the MCV deploy order given at once.
    offset = ex.deploy_frame or 0
    rows = compare(predicted.events, ex.events, offset)
    print(f"\n{'項目':<14}{'預測開始':>8}{'實際開始':>8}{'預測完成':>8}{'實際完成':>8}{'誤差':>7}")
    for r in rows:
        fmt = lambda v: "-" if v is None else str(v)
        print(f"{r['type'] + ' #' + str(r['n']):<14}{r['pred_start']:>10}{fmt(r['start']):>10}"
              f"{r['pred_done']:>10}{fmt(r['done']):>10}{fmt(r['diff']):>8}")
    finished = ex.done
    last = max((r["done"] for r in rows if r["done"] is not None), default=None)
    if finished and last is not None and predicted.finish_frame:
        err = (last - predicted.finish_frame) / predicted.finish_frame
        print(f"\n全部完成：實際第 {last} 幀，預測第 {predicted.finish_frame} 幀（誤差 {err:+.1%}，"
              f"已扣掉開始前的 {offset} 幀）")
    else:
        print(f"\n逾時，未完成全部項目")

    measured = measured_delays(db, ex.events, offset)
    if measured:
        print("實測（含輪詢延遲）：" + "，".join(f"{k} = {v}" for k, v in measured.items()))

    log = Path(args.log) if args.log else ROOT / "logs" / f"execute_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log.parent.mkdir(exist_ok=True)
    log.write_text(json.dumps({"country": country, "sequence": seq, "targets": targets,
                               "deploy_frame": offset, "predicted": predicted.events,
                               "actual": ex.events, "rows": rows, "finished": finished,
                               "measured": measured},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"記錄已存到 {log}")


if __name__ == "__main__":
    main()
