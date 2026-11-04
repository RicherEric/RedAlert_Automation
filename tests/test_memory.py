"""GameMemory parsing against a hand-built fake memory image."""
import struct

import pytest

from ra2tools import yr_layout as L
from ra2tools.memory import GameMemory


class FakeMemory:
    def __init__(self):
        self.mem: dict[int, int] = {}

    def write(self, addr: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def i32(self, addr: int, v: int) -> None:
        self.write(addr, struct.pack("<i", v))

    def read_bytes(self, addr: int, size: int) -> bytes:
        return bytes(self.mem.get(addr + i, 0) for i in range(size))


def build_world() -> FakeMemory:
    m = FakeMemory()
    m.i32(L.CURRENT_FRAME, 1234)
    m.i32(L.CURRENT_FRAME_RATE, 30)

    def type_obj(addr, ident):
        m.write(addr + L.TYPE_ID, ident.encode() + b"\0")
        return addr

    russians = type_obj(0x100000, "Russians")
    americans = type_obj(0x101000, "Americans")
    htnk = type_obj(0x102000, "HTNK")
    mtnk = type_obj(0x103000, "MTNK")
    napowr = type_obj(0x104000, "NAPOWR")

    house_a, house_b = 0x200000, 0x220000
    for addr, idx, typ, credits, human in ((house_a, 0, russians, 5000, 1), (house_b, 1, americans, 7000, 0)):
        m.i32(addr + L.HOUSE_ARRAY_INDEX, idx)
        m.i32(addr + L.HOUSE_TYPE, typ)
        m.i32(addr + L.HOUSE_BALANCE, credits)
        m.i32(addr + L.HOUSE_POWER_OUTPUT, 150)
        m.i32(addr + L.HOUSE_POWER_DRAIN, 85)
        m.write(addr + L.HOUSE_IS_HUMAN, bytes([human]))
    m.i32(L.CURRENT_PLAYER, house_a)

    def vector(array_addr, items_addr, ptrs):
        m.i32(array_addr + L.DVC_ITEMS, items_addr if ptrs else 0)
        m.i32(array_addr + L.DVC_COUNT, len(ptrs))
        for i, p in enumerate(ptrs):
            m.i32(items_addr + 4 * i, p)

    vector(L.HOUSE_ARRAY, 0x300000, [house_a, house_b])

    def unit(addr, typ, owner, x_cells, kind="vehicle"):
        m.i32(addr + L.TYPE_PTR[kind], typ)
        m.i32(addr + L.TECHNO_OWNER, owner)
        m.i32(addr + L.OBJ_HEALTH, 400)
        m.write(addr + L.OBJ_LOCATION, struct.pack("<3i", int(x_cells * 256), 512, 0))
        return addr

    units = [unit(0x400000, htnk, house_a, 10), unit(0x401000, htnk, house_a, 11),
             unit(0x402000, mtnk, house_b, 50)]
    vector(L.UNIT_ARRAY, 0x310000, units)
    for arr in (L.INFANTRY_ARRAY, L.BUILDING_ARRAY, L.AIRCRAFT_ARRAY):
        vector(arr, 0, [])

    # A vehicle factory building a Rhino at half progress.
    factory = 0x500000
    m.i32(house_a + L.HOUSE_FACTORIES["vehicle"], factory)
    building_rhino = unit(0x403000, htnk, house_a, 0)
    m.i32(factory + L.FACTORY_OBJECT, building_rhino)
    m.i32(factory + L.FACTORY_PROGRESS, L.PRODUCTION_STEPS // 2)
    # A building factory that is idle.
    m.i32(house_a + L.HOUSE_FACTORIES["building"], 0x510000)
    _ = napowr
    return m


def test_snapshot_reads_houses_objects_and_factories():
    snap = GameMemory(build_world()).snapshot()
    assert snap.frame == 1234 and snap.frame_rate == 30
    assert [h.country for h in snap.houses] == ["Russians", "Americans"]
    assert snap.player.credits == 5000 and snap.player.is_human
    assert snap.player.power_output == 150 and snap.player.power_drain == 85
    assert snap.counts(0) == {"HTNK": 2}
    assert snap.counts(1) == {"MTNK": 1}
    assert snap.objects[0].x == 10 and snap.objects[0].y == 2
    vehicle = next(f for f in snap.factories if f.category == "vehicle")
    assert vehicle.type_id == "HTNK" and abs(vehicle.progress - 27 / 54) < 1e-9
    idle = next(f for f in snap.factories if f.category == "building")
    assert idle.type_id is None


def test_menu_screen_has_no_player():
    m = build_world()
    m.i32(L.CURRENT_PLAYER, 0)
    gm = GameMemory(m)
    assert not gm.in_game()
    assert gm.snapshot().player is None


def test_live_fps_ignores_pauses_and_resets_on_speed_change():
    from ra2tools.memory import LiveFps

    live = LiveFps(window=5.0)
    for i in range(20):  # 30 fps
        live.update(i * 0.5, i * 15, speed=3)
    assert live.fps == pytest.approx(30.0)
    for i in range(20, 30):  # paused: wall time moves, frames do not
        live.update(i * 0.5, 19 * 15, speed=3)
    assert live.fps == pytest.approx(30.0)
    live.update(15.0, 300, speed=1)  # speed setting changed -> fresh window
    assert live.fps is None
    live.update(15.5, 330, speed=1)
    assert live.fps == pytest.approx(60.0)
