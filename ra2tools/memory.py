"""Read live Yuri's Revenge state from gamemd.exe memory (read-only).

All reads go through a `Reader` (anything with read_bytes(address, size)), so the parsing can
be tested against a fake memory image. `attach()` returns a reader backed by pymem.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Protocol

from . import yr_layout as L


class Reader(Protocol):
    def read_bytes(self, address: int, size: int) -> bytes: ...


class GameNotFound(RuntimeError):
    pass


class LiveFps:
    """Rolling frames-per-second from (wall time, frame) samples, ignoring pauses.

    Only stretches where the frame counter advanced count, so pausing (or the single-player
    auto-pause when the window loses focus) does not drag the estimate down. A change of the
    game-speed setting, a new game, or a long pause starts a fresh window.
    """

    def __init__(self, window: float = 5.0):
        self.window = window
        self._steps: list[tuple[float, int]] = []  # (seconds, frames) of advancing intervals
        self._last: tuple[float, int] | None = None
        self._speed: int | None = None

    def update(self, wall: float, frame: int, speed: int | None = None) -> float | None:
        if speed != self._speed or (self._last and frame < self._last[1]):
            self._steps.clear()
            self._speed = speed
        elif self._last:
            d_wall, d_frame = wall - self._last[0], frame - self._last[1]
            if d_frame > 0 and d_wall > 0:
                self._steps.append((d_wall, d_frame))
                while sum(s for s, _ in self._steps[1:]) >= self.window:
                    self._steps.pop(0)
        self._last = (wall, frame)
        return self.fps

    @property
    def fps(self) -> float | None:
        secs = sum(s for s, _ in self._steps)
        return sum(f for _, f in self._steps) / secs if secs > 0 else None


def attach(process_name: str = "gamemd.exe") -> Reader:
    import pymem
    import pymem.exception

    try:
        pm = pymem.Pymem(process_name)
    except pymem.exception.ProcessNotFound as e:
        raise GameNotFound(f"找不到 {process_name}，請先開啟尤里的復仇並進入遊戲") from e
    return pm


DIFFICULTIES = {0: "hard", 1: "medium", 2: "easy"}  # the engine stores them in reverse


@dataclass
class HouseState:
    address: int
    index: int
    country: str
    is_human: bool
    is_player: bool
    defeated: bool
    credits: int
    power_output: int
    power_drain: int
    ai_difficulty: str | None = None  # easy / medium / hard for AI houses


@dataclass
class ObjectState:
    address: int
    kind: str      # vehicle / infantry / building / aircraft
    type_id: str
    owner: int     # house array index
    health: int
    x: float       # cells
    y: float
    on_map: bool
    selected: bool


@dataclass
class FactoryState:
    category: str
    type_id: str | None
    progress: float  # 0..1
    on_hold: bool


@dataclass
class Snapshot:
    wall_time: float
    frame: int
    frame_rate: int
    player_index: int | None
    houses: list[HouseState]
    objects: list[ObjectState] = field(default_factory=list)
    factories: list[FactoryState] = field(default_factory=list)
    game_speed: int | None = None  # the in-game speed setting (GameOptionsClass::GameSpeed)

    @property
    def player(self) -> HouseState | None:
        return next((h for h in self.houses if h.index == self.player_index), None)

    def counts(self, owner: int) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.objects:
            if o.owner == owner and o.health > 0:
                out[o.type_id] = out.get(o.type_id, 0) + 1
        return out


class GameMemory:
    def __init__(self, reader: Reader):
        self.r = reader
        self._type_cache: dict[int, str] = {}
        self._house_index: dict[int, int] = {}

    # ---------------------------------------------------------------- primitives
    def _raw(self, addr: int, size: int) -> bytes:
        return self.r.read_bytes(addr, size)

    def i32(self, addr: int) -> int:
        return struct.unpack("<i", self._raw(addr, 4))[0]

    def u32(self, addr: int) -> int:
        return struct.unpack("<I", self._raw(addr, 4))[0]

    def u8(self, addr: int) -> int:
        return self._raw(addr, 1)[0]

    def cstr(self, addr: int, size: int) -> str:
        return self._raw(addr, size).split(b"\0", 1)[0].decode("latin-1")

    def vector(self, addr: int) -> list[int]:
        """Pointers held by a DynamicVectorClass<T*> at addr."""
        count = self.i32(addr + L.DVC_COUNT)
        items = self.u32(addr + L.DVC_ITEMS)
        if count <= 0 or not items:
            return []
        if count > 100000:
            raise ValueError(f"implausible vector size {count} at {addr:#x}")
        return list(struct.unpack(f"<{count}I", self._raw(items, 4 * count)))

    def type_id(self, type_ptr: int) -> str:
        if not type_ptr:
            return ""
        if type_ptr not in self._type_cache:
            self._type_cache[type_ptr] = self.cstr(type_ptr + L.TYPE_ID, L.TYPE_ID_LEN)
        return self._type_cache[type_ptr]

    # ------------------------------------------------------------------- reading
    def frame(self) -> int:
        return self.i32(L.CURRENT_FRAME)

    def frame_rate(self) -> int:
        return self.u32(L.CURRENT_FRAME_RATE)

    def game_speed(self) -> int:
        return self.i32(L.GAME_SPEED)

    def in_game(self) -> bool:
        return self.u32(L.CURRENT_PLAYER) != 0

    def houses(self) -> list[HouseState]:
        out = []
        for ptr in self.vector(L.HOUSE_ARRAY):
            idx = self.i32(ptr + L.HOUSE_ARRAY_INDEX)
            self._house_index[ptr] = idx
            out.append(HouseState(
                address=ptr,
                index=idx,
                country=self.type_id(self.u32(ptr + L.HOUSE_TYPE)),
                is_human=bool(self.u8(ptr + L.HOUSE_IS_HUMAN)),
                is_player=bool(self.u8(ptr + L.HOUSE_IS_PLAYER_CONTROL)),
                defeated=bool(self.u8(ptr + L.HOUSE_DEFEATED)),
                credits=self.i32(ptr + L.HOUSE_BALANCE),
                power_output=self.i32(ptr + L.HOUSE_POWER_OUTPUT),
                power_drain=self.i32(ptr + L.HOUSE_POWER_DRAIN),
                ai_difficulty=None if self.u8(ptr + L.HOUSE_IS_HUMAN)
                else DIFFICULTIES.get(self.i32(ptr + L.HOUSE_AI_DIFFICULTY)),
            ))
        return out

    def objects(self) -> list[ObjectState]:
        out = []
        for kind, array in L.OBJECT_ARRAYS.items():
            for ptr in self.vector(array):
                owner_ptr = self.u32(ptr + L.TECHNO_OWNER)
                x, y, _z = struct.unpack("<3i", self._raw(ptr + L.OBJ_LOCATION, 12))
                out.append(ObjectState(
                    address=ptr,
                    kind=kind,
                    type_id=self.type_id(self.u32(ptr + L.TYPE_PTR[kind])),
                    owner=self._house_index.get(owner_ptr, -1),
                    health=self.i32(ptr + L.OBJ_HEALTH),
                    x=x / L.LEPTONS_PER_CELL,
                    y=y / L.LEPTONS_PER_CELL,
                    on_map=bool(self.u8(ptr + L.OBJ_IS_ON_MAP)),
                    selected=bool(self.u8(ptr + L.OBJ_IS_SELECTED)),
                ))
        return out

    def factories(self, house: HouseState) -> list[FactoryState]:
        out = []
        for cat, off in L.HOUSE_FACTORIES.items():
            fptr = self.u32(house.address + off)
            if not fptr:
                continue
            obj = self.u32(fptr + L.FACTORY_OBJECT)
            type_id = None
            if obj:
                # The object under construction is a Techno; find its kind via its type pointer.
                kind = "building" if cat == "defense" else cat if cat in L.TYPE_PTR else "vehicle"
                type_id = self.type_id(self.u32(obj + L.TYPE_PTR[kind]))
            value = self.i32(fptr + L.FACTORY_PROGRESS)
            out.append(FactoryState(cat, type_id, value / L.PRODUCTION_STEPS,
                                    bool(self.u8(fptr + L.FACTORY_ON_HOLD))))
        return out

    def snapshot(self, with_objects: bool = True) -> Snapshot:
        player_ptr = self.u32(L.CURRENT_PLAYER)
        houses = self.houses()
        player = next((h for h in houses if h.address == player_ptr), None)
        snap = Snapshot(time.perf_counter(), self.frame(), self.frame_rate(),
                        player.index if player else None, houses, game_speed=self.game_speed())
        if with_objects:
            snap.objects = self.objects()
        if player:
            snap.factories = self.factories(player)
        return snap

    # ------------------------------------------------------------------ checking
    def sanity_check(self, wait: float = 1.0) -> list[str]:
        """Return a list of problems; empty means the layout looks right for this exe."""
        problems = []
        f0 = self.frame()
        time.sleep(wait)
        f1 = self.frame()
        if f1 <= f0:
            problems.append(f"幀計數沒有前進（{f0} → {f1}）：遊戲暫停中（單人模式切到別的視窗會自動暫停），或位址不符合這個版本")
        if not self.in_game():
            problems.append("CurrentPlayer 是空指標：還在選單畫面？請進入一場遭遇戰")
            return problems
        houses = self.houses()
        if not 1 <= len(houses) <= 16:
            problems.append(f"陣營數量不合理：{len(houses)}")
        known = {"Americans", "Alliance", "French", "Germans", "British", "Russians",
                 "Confederation", "Africans", "Arabs", "YuriCountry"}
        if houses and not any(h.country in known for h in houses):
            problems.append(f"讀不到已知的國家名稱：{[h.country for h in houses]}")
        player_ptr = self.u32(L.CURRENT_PLAYER)
        if houses and player_ptr not in {h.address for h in houses}:
            problems.append("CurrentPlayer 不在陣營清單裡")
        for h in houses:
            if not -1 <= h.credits <= 10_000_000:
                problems.append(f"陣營 {h.index} 金錢不合理：{h.credits}")
        return problems
