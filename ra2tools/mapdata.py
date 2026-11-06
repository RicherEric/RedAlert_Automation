"""Ore and gem fields of a multiplayer map (.map/.mpr), from its [OverlayPack].

The pack is base64 of chunks (uint16 compressed size, uint16 output size, LCW/format80 data)
that unpack to one overlay index per cell, 512 x 512, cell (x, y) at x + 512 * y; 0xFF is empty.
[OverlayDataPack] holds the density (0-11) of each cell the same way. Rules list the overlay
types in [OverlayTypes]; the index is the position in that list (the keys start at 1): TIB01-TIB20 ore (Value 25), GEM01-GEM12 gems (Value 50).
Cells are in the same units as memory.ObjectState x/y.
"""
from __future__ import annotations

import base64
import math
import struct
from dataclasses import dataclass

from .ini import Ini as IniFile

SIZE = 512


def lcw_decode(src: bytes, out_size: int) -> bytes:
    """Westwood format80 (LCW), absolute-offset variant used by map packs."""
    dst = bytearray()
    i = 0
    while i < len(src) and len(dst) < out_size:
        c = src[i]
        i += 1
        if c == 0x80:
            break
        if not c & 0x80:  # 0cccpppp p: copy c+3 from relative position
            count = (c >> 4) + 3
            pos = len(dst) - (((c & 0x0F) << 8) | src[i])
            i += 1
            for k in range(count):
                dst.append(dst[pos + k])
        elif not c & 0x40:  # 10cccccc: copy c bytes from the source
            count = c & 0x3F
            dst += src[i:i + count]
            i += count
        elif c == 0xFE:  # fill
            count, value = struct.unpack_from("<H", src, i)[0], src[i + 2]
            i += 3
            dst += bytes([value]) * count
        elif c == 0xFF:  # long copy from absolute position
            count, pos = struct.unpack_from("<HH", src, i)
            i += 4
            for k in range(count):
                dst.append(dst[pos + k])
        else:  # 11cccccc pppp: copy c+3 from absolute position
            count = (c & 0x3F) + 3
            pos = struct.unpack_from("<H", src, i)[0]
            i += 2
            for k in range(count):
                dst.append(dst[pos + k])
    return bytes(dst)


def unpack(ini: IniFile, section: str) -> bytes:
    raw = base64.b64decode("".join(v for _, v in sorted(ini.section(section).items(), key=lambda kv: int(kv[0]))))
    out = bytearray()
    i = 0
    while i + 4 <= len(raw):
        csize, usize = struct.unpack_from("<HH", raw, i)
        i += 4
        out += lcw_decode(raw[i:i + csize], usize)
        i += csize
    return bytes(out)


@dataclass
class Field:
    kind: str                 # "ore" or "gem"
    cells: list[tuple[int, int, int]]  # (x, y, bales)
    value: int                # credits per bale

    @property
    def centre(self) -> tuple[float, float]:
        w = sum(b for *_, b in self.cells) or 1
        return (sum(x * b for x, _, b in self.cells) / w, sum(y * b for _, y, b in self.cells) / w)

    @property
    def credits(self) -> int:
        return sum(b for *_, b in self.cells) * self.value


def overlay_kinds(rules: IniFile) -> dict[int, tuple[str, int]]:
    """Overlay index -> ("ore"|"gem", credits per bale) from [OverlayTypes] and the [Tiberiums]."""
    names = dict(enumerate(rules.registry("OverlayTypes")))  # the index is the position, not the key
    value = lambda section, default: int(rules.get(section, "Value", default) or default)
    ore, gem = value("Riparius", 25), value("Cruentus", 50)
    out = {}
    for i, name in names.items():
        if name.startswith("GEM"):
            out[i] = ("gem", gem)
        elif name.startswith("TIB"):
            out[i] = ("ore", ore)
    return out


def fields(map_ini: IniFile, rules: IniFile, join: float = 3.0) -> list[Field]:
    """Ore/gem cells grouped into fields (cells within `join` cells of each other)."""
    overlay, density = unpack(map_ini, "OverlayPack"), unpack(map_ini, "OverlayDataPack")
    kinds = overlay_kinds(rules)
    cells = []
    for idx, o in enumerate(overlay):
        if o in kinds:
            cells.append((idx % SIZE, idx // SIZE, density[idx] + 1 if idx < len(density) else 1, *kinds[o]))
    groups: list[list] = []
    for c in cells:
        near = [g for g in groups if g[0][3] == c[3] and any(math.dist(c[:2], d[:2]) <= join for d in g)]
        merged = [c]
        for g in near:
            merged += g
            groups.remove(g)
        groups.append(merged)
    return sorted((Field(g[0][3], [(x, y, b) for x, y, b, *_ in g], g[0][4]) for g in groups),
                  key=lambda f: -f.credits)


def load_fields(name: str = "mp29u2.map", game_dir=None, rules_path=None) -> list[Field]:
    """Fields of a map in the game's MIX files (default: the skirmish map autostart.py uses)."""
    from pathlib import Path

    from .launcher import DEFAULT_GAME_DIR
    from .mix import MixLibrary
    found = MixLibrary(Path(game_dir or DEFAULT_GAME_DIR)).find(name)
    if found is None:
        return []
    rules = Path(rules_path) if rules_path else Path(__file__).resolve().parent.parent / "data" / "rulesmd.ini"
    return fields(IniFile.from_bytes(found[1]), IniFile.from_bytes(rules.read_bytes()))
