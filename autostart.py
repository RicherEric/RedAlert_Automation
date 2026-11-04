"""Start Yuri's Revenge with a fixed test profile and enter a skirmish automatically.

    .venv\\Scripts\\python.exe autostart.py start     # back up RA2MD.INI, apply the test profile, play
    .venv\\Scripts\\python.exe autostart.py stop      # close the game and restore your RA2MD.INI
    .venv\\Scripts\\python.exe autostart.py remove-spawner   # take the spawner files out of the game dir

Two ways in. Spawner (default once tools\\build_spawner.bat has run): Syringe starts gamemd.exe with
the CnCNet spawner DLL, which reads SPAWN.INI and goes straight into the skirmish, no menus and no
mouse. Menu (--menu, or when the spawner is not built): the script clicks through the menus with
the real cursor, so do not touch mouse/keyboard for ~15 s. Either way the game runs windowed (-WIN)
at 800x600 and keeps running while another window has focus.
"""
from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ra2tools import launcher as L
from ra2tools.memory import GameMemory, GameNotFound, attach
from ra2tools.mix import MixLibrary

ROOT = Path(__file__).parent
REFS = ROOT / "tools" / "autostart_refs"
TITLE_BOX = (660, 4, 775, 30)  # menu title area, in 800x600 menu coordinates
BACKUP_SUFFIX = ".autostart-backup"

# Fixed test profile written into RA2MD.INI. SlotNN = "<player type>,<country>,<color>" as saved
# by the skirmish screen: Slot01 is the AI opponent (6 = easy AI, country 0 = Americans), the
# remaining slots are closed (1). Start positions are not saved, see SETUP below.
# AI player types by difficulty, all verified against HouseClass::AIDifficulty (memory) on
# 2026-11-01; the difficulty is still checked and reported after every start.
AI_TYPES = {"easy": 6, "medium": 5, "hard": 4}
PROFILE = {
    "Intro": {"Play": "no"},
    # Video.Windowed: the spawner takes windowed mode from here, not from -WIN; without it the
    # game pauses whenever its window loses focus (2026-11-01, two frozen test games).
    "Video": {"ScreenWidth": "800", "ScreenHeight": "600", "Video.Windowed": "yes"},
    "Options": {"GameSpeed": "3"},
    "MultiPlayer": {"Side": "Russians"},
    "Skirmish": {
        "ScenIndex": "0", "GameSpeed": "3", "Credits": "10000", "UnitCount": "10",
        "ShortGame": "yes", "SuperWeaponsAllowed": "yes", "BuildOffAlly": "yes",
        "MCVRepacks": "yes", "CratesAppear": "yes",
        "Slot01": "6,0,-2", **{f"Slot0{i}": "1,-2,-2" for i in range(2, 8)},
    },
}

# Clicks on the skirmish screen before starting: our start position = 2 (map 火線衝突(2), the
# top-right base at about cell 68,17). The INI does not store positions, so they are clicked.
# Each click is followed by checking a box against a reference crop.
SETUP = [
    ((515, 70), None),                                  # open our 位置 dropdown
    ((491, 142), ("skirmish_pos2", (486, 62, 500, 78))),  # pick "2"
]

# Spawner launch: files copied into the game dir, built from source by tools\build_spawner.bat
# (CnCNet yrpp-spawner, GPL-3; Syringe, LGPL-3). With one human player the spawner sets the
# session to Skirmish, so the bridge's refusal of Internet games still holds.
SPAWNER_FILES = {
    "Syringe.exe": ROOT / "tools" / "third_party" / "Syringe" / "Release" / "Syringe.exe",
    "CnCNet-Spawner.dll": ROOT / "tools" / "third_party" / "yrpp-spawner" / "Release" / "CnCNet-Spawner.dll",
}
# 火線衝突（2） (DESC:MP29U2), the map at ScenIndex 0 in the menu. It sits in multi.mix, which the
# spawner cannot load from ("無法讀取劇情場景"), so like the CnCNet client it is copied out as a
# loose spawnmap.ini.
SPAWN_MAP = "mp29u2.map"
SPAWN_MAP_FILE = "spawnmap.ini"
SPAWN_POSITION = 1         # 0-based: the menu's position 2
COUNTRIES = {"Americans": 0, "Russians": 8}
# AISlots difficulty, as HouseClass::AIDifficulty (0 = hard); checked from memory after the start.
SPAWN_DIFFICULTY = {"easy": 2, "medium": 1, "hard": 0}


def spawn_ini(ai: str, seed: int) -> str:
    return "\n".join([
        "[Settings]", "Name=Bot", f"Scenario={SPAWN_MAP_FILE}", "GameMode=1", "GameSpeed=3",
        "Credits=10000", "UnitCount=10", "Bases=Yes", "ShortGame=Yes", "Superweapons=Yes",
        "Crates=Yes", "MCVRedeploy=Yes", "BuildOffAlly=Yes", "AIPlayers=1", f"Seed={seed}",
        f"Side={COUNTRIES['Russians']}", "Color=0", "SkipScoreScreen=Yes", "",
        "[HouseCountries]", f"Multi2={COUNTRIES['Americans']}", "",
        "[HouseColors]", "Multi2=1", "",
        "[HouseHandicaps]", f"Multi2={SPAWN_DIFFICULTY[ai]}", "",
        "[SpawnLocations]", f"Multi1={SPAWN_POSITION}", f"Multi2={1 - SPAWN_POSITION}", "",
    ])


def spawner_built() -> bool:
    return all(p.exists() for p in SPAWNER_FILES.values())


def launch_spawner(game_dir: Path, ai: str) -> int:
    for name, src in SPAWNER_FILES.items():
        dst = game_dir / name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copy2(src, dst)
    found = MixLibrary(game_dir).find(SPAWN_MAP)
    if found is None:
        raise RuntimeError(f"遊戲資料裡找不到地圖 {SPAWN_MAP}")
    (game_dir / SPAWN_MAP_FILE).write_bytes(found[1])
    (game_dir / "SPAWN.INI").write_text(spawn_ini(ai, random.randrange(1, 2**31)), encoding="latin-1")
    # Syringe takes the raw command line: "<exe>" <arguments>.
    subprocess.Popen(f'"{game_dir / "Syringe.exe"}" "gamemd.exe" -SPAWN -WIN', cwd=game_dir)
    end = time.monotonic() + 60
    while time.monotonic() < end:
        hwnd = L.game_window()
        if hwnd:
            return hwnd
        time.sleep(0.5)
    raise RuntimeError("等不到遊戲視窗（看 syringe.log）")


# (screen we must be on, button to click in menu coordinates, screen expected afterwards)
STEPS = [
    ("main", (716, 220), "single"),      # 單人遊戲
    ("single", (716, 304), "skirmish"),  # 遭遇戰
    ("skirmish", (716, 262), None),      # 開始遊戲 -> loading, then in game
]


def apply_profile(ini_path: Path, profile: dict[str, dict[str, str]]) -> None:
    """Set keys in an INI file in place, keeping everything else (order, other sections)."""
    lines = ini_path.read_text(encoding="latin-1").splitlines() if ini_path.exists() else []
    pending = {s: dict(kv) for s, kv in profile.items()}
    out: list[str] = []
    section = None

    def flush(sec):
        for k, v in pending.pop(sec, {}).items():
            out.append(f"{k}={v}")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section in pending:
                flush(section)
            section = stripped[1:-1]
        elif section in pending and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in pending[section]:
                line = f"{key}={pending[section].pop(key)}"
        out.append(line)
    if section in pending:
        flush(section)
    for sec in list(pending):
        out += ["", f"[{sec}]"]
        flush(sec)
    ini_path.write_text("\n".join(out) + "\n", encoding="latin-1")


def which_screen() -> tuple[str | None, float]:
    """Best-matching menu screen and its mean pixel difference (lower is better)."""
    from PIL import Image, ImageChops, ImageStat

    x0, y0 = L.to_screen(TITLE_BOX[0], TITLE_BOX[1])
    x1, y1 = L.to_screen(TITLE_BOX[2], TITLE_BOX[3])
    shot = L.screenshot().crop((x0, y0, x1, y1)).convert("RGB")
    best, best_diff = None, 1e9
    for ref in REFS.glob("*.png"):
        img = Image.open(ref).convert("RGB").resize(shot.size)
        diff = sum(ImageStat.Stat(ImageChops.difference(shot, img)).mean) / 3
        if diff < best_diff:
            best, best_diff = ref.stem, diff
    return (best if best_diff < 10 else None), best_diff


def box_matches(ref: str, box: tuple[int, int, int, int]) -> float:
    """Mean pixel difference between a menu box and tools/autostart_refs/<ref>.png."""
    from PIL import Image, ImageChops, ImageStat

    x0, y0 = L.to_screen(box[0], box[1])
    x1, y1 = L.to_screen(box[2], box[3])
    shot = L.screenshot().crop((x0, y0, x1, y1)).convert("RGB")
    img = Image.open(REFS / "boxes" / f"{ref}.png").convert("RGB").resize(shot.size)
    return sum(ImageStat.Stat(ImageChops.difference(shot, img)).mean) / 3


def setup_skirmish() -> None:
    for (gx, gy), check in SETUP:
        L.click(gx, gy)
        time.sleep(0.6)
        if check:
            diff = box_matches(*check)
            if diff >= 10:
                raise RuntimeError(f"遭遇戰設定沒有生效（{check[0]} 差異 {diff:.1f}）")
    print("  skirmish → 出生位置 2")


def wait_for_screen(name: str, timeout: float) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if which_screen()[0] == name:
            return
        time.sleep(0.5)
    screen, diff = which_screen()
    raise RuntimeError(f"等不到「{name}」畫面（目前像是 {screen}，差異 {diff:.1f}）")


def wait_in_game(timeout: float) -> GameMemory:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            gm = GameMemory(attach())
            if gm.in_game() and gm.frame() > 0:
                return gm
        except GameNotFound:
            pass
        time.sleep(1)
    raise RuntimeError("按了開始遊戲，但沒有進入對戰")


def start(args) -> None:
    ini = args.game_dir / "RA2MD.INI"
    if L.process_ids():
        sys.exit("遊戲已經在執行，請先關閉（或用 autostart.py stop）")
    backup = ini.with_name(ini.name + BACKUP_SUFFIX)
    if ini.exists() and not backup.exists():
        shutil.copy2(ini, backup)
        print(f"已備份你的設定到 {backup.name}")
    profile = {**PROFILE, "Skirmish": {**PROFILE["Skirmish"], "Slot01": f"{AI_TYPES[args.ai]},0,-2"}}
    apply_profile(ini, profile)
    if not args.menu and spawner_built():
        launch_spawner(args.game_dir, args.ai)
        print("遊戲已用 spawner 啟動（不需要滑鼠），等待進入對戰...")
        report(wait_in_game(timeout=120), args)
        return
    hwnd = L.launch(args.game_dir)
    print("遊戲已啟動，等待主選單...")
    wait_for_screen("main", timeout=90)
    for screen, (gx, gy), expect in STEPS:
        wait_for_screen(screen, timeout=15)
        if screen == getattr(args, "stop_at", None):
            print(f"已停在「{screen}」畫面")
            return
        if not L.focus(hwnd):
            raise RuntimeError("無法把遊戲切到前景")
        if screen == "skirmish":
            setup_skirmish()
        L.click(gx, gy)
        print(f"  {screen} → 點擊 ({gx}, {gy})")
        if expect:
            wait_for_screen(expect, timeout=15)
    report(wait_in_game(timeout=60), args)


def report(gm: GameMemory, args) -> None:
    snap = gm.snapshot()
    sides = ", ".join(f"{h.country}{'（你）' if h.is_player else ''}" for h in snap.houses
                      if h.country not in ("Neutral", "Special"))
    mcv = next((o for o in snap.objects if o.owner == snap.player_index and o.type_id.endswith("MCV")), None)
    where = f"，基地車在 ({mcv.x:.0f}, {mcv.y:.0f})" if mcv else ""
    print(f"已進入遭遇戰：第 {snap.frame} 幀，速度設定 {snap.game_speed}，{sides}{where}")
    ai = next((h for h in snap.houses if not h.is_human and h.country not in ("Neutral", "Special")), None)
    if ai is not None:
        ok = "" if ai.ai_difficulty == args.ai else f"（要求的是 {args.ai}：難度代碼對應有誤）"
        print(f"AI 難度：{ai.ai_difficulty}{ok}")


def stop(args) -> None:
    for pid in L.process_ids() + L.process_ids("RA2MD.exe") + L.process_ids("Syringe.exe"):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    for name in ("SPAWN.INI", SPAWN_MAP_FILE):
        (args.game_dir / name).unlink(missing_ok=True)
    ini = args.game_dir / "RA2MD.INI"
    backup = ini.with_name(ini.name + BACKUP_SUFFIX)
    if backup.exists():
        time.sleep(1)
        shutil.move(backup, ini)
        print("遊戲已關閉，已還原你的 RA2MD.INI")
    else:
        print("遊戲已關閉（沒有需要還原的備份）")


def remove_spawner(args) -> None:
    for name in [*SPAWNER_FILES, "SPAWN.INI", SPAWN_MAP_FILE, "syringe.log"]:
        path = args.game_dir / name
        if path.exists():
            path.unlink()
            print(f"已刪除 {path}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Auto-start a Yuri's Revenge skirmish (test profile)")
    ap.add_argument("cmd", choices=["start", "stop", "remove-spawner"])
    ap.add_argument("--game-dir", type=Path, default=L.DEFAULT_GAME_DIR)
    ap.add_argument("--ai", choices=list(AI_TYPES), default="easy", help="AI 難度")
    ap.add_argument("--menu", action="store_true", help="點選單進入（不用 spawner）")
    ap.add_argument("--stop-at", choices=[s for s, _, _ in STEPS], help="停在這個選單畫面（不點擊）")
    args = ap.parse_args()
    if args.stop_at:
        args.menu = True
    try:
        {"start": start, "stop": stop, "remove-spawner": remove_spawner}[args.cmd](args)
    except RuntimeError as e:
        sys.exit(f"失敗：{e}")


if __name__ == "__main__":
    main()
