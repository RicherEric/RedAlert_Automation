"""Start Yuri's Revenge and click through the menus into a skirmish (Windows only).

The game runs at 800x600 and is scaled to the screen height and centred, so every position here is
in 800x600 game coordinates. Menus are recognised by sampling a few pixels of the screenshot; the
skirmish settings themselves come from the [Skirmish] section of RA2MD.INI (the last ones used).
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

DEFAULT_GAME_DIR = Path(r"D:\SteamLibrary\steamapps\common\Command & Conquer Red Alert II")

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12


def process_ids(name: str = "gamemd.exe") -> list[int]:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [int(line.split(",")[1].strip('"')) for line in out.splitlines() if name.lower() in line.lower()]


def game_window(pid: int | None = None) -> int | None:
    """Top-level visible window of gamemd.exe."""
    pids = {pid} if pid else set(process_ids())
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def focus(hwnd: int) -> bool:
    """Bring the game to the foreground (pressing Alt lifts Windows' focus-stealing lock)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def game_resolution(game_dir: Path = DEFAULT_GAME_DIR) -> tuple[int, int]:
    """ScreenWidth/ScreenHeight from RA2MD.INI (what the game renders at)."""
    w, h = 800, 600
    ini = game_dir / "RA2MD.INI"
    if ini.exists():
        for line in ini.read_text(encoding="latin-1").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "ScreenWidth" and value.strip().isdigit():
                w = int(value)
            elif key.strip() == "ScreenHeight" and value.strip().isdigit():
                h = int(value)
    return w, h


def geometry(hwnd: int | None = None) -> tuple[float, float, float]:
    """(left, top, scale) of the game image on the screen.

    Windowed (-WIN): the image sits at its native size at the bottom-centre of the window rect
    (title bar above, equal borders on the sides). Fullscreen: it is scaled to the screen height
    and centred horizontally.
    """
    rw, rh = game_resolution()
    hwnd = hwnd or game_window()
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if hwnd:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        ww, wh = r.right - r.left, r.bottom - r.top
        if (ww, wh) != (sw, sh) and ww >= rw and wh >= rh:
            border = (ww - rw) / 2
            return r.left + border, r.bottom - border - rh, 1.0
    scale = sh / rh
    return (sw - rw * scale) / 2, 0.0, scale


def to_screen(gx: float, gy: float, hwnd: int | None = None) -> tuple[int, int]:
    """Menu coordinates (an 800x600 layout centred in the game image) -> screen pixels."""
    left, top, scale = geometry(hwnd)
    rw, rh = game_resolution()
    return int(left + ((rw - 800) / 2 + gx) * scale), int(top + ((rh - 600) / 2 + gy) * scale)


def screenshot():
    from PIL import ImageGrab
    return ImageGrab.grab()


def pixel(img, gx: float, gy: float) -> tuple[int, int, int]:
    return img.getpixel(to_screen(gx, gy))[:3]


def click(gx: float, gy: float) -> None:
    sx, sy = to_screen(gx, gy)
    user32.SetCursorPos(sx, sy)
    time.sleep(0.1)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def launch(game_dir: Path = DEFAULT_GAME_DIR, timeout: float = 60, windowed: bool = True) -> int:
    """Start gamemd.exe (-WIN: windowed, keeps running without focus) unless already running."""
    if not process_ids():
        subprocess.Popen([str(game_dir / "gamemd.exe")] + (["-WIN"] if windowed else []), cwd=game_dir)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        hwnd = game_window()
        if hwnd:
            return hwnd
        time.sleep(0.5)
    raise TimeoutError("等不到遊戲視窗")
