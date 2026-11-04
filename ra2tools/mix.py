"""Westwood MIX archive reader (Red Alert 2 / Yuri's Revenge).

Supports plain and Blowfish-encrypted headers, and nested MIX files.
"""
from __future__ import annotations

import base64
import binascii
import struct
from pathlib import Path

from Crypto.Cipher import Blowfish

FLAG_CHECKSUM = 0x00010000
FLAG_ENCRYPTED = 0x00020000

# Westwood's public key, as used by XCC Utilities (DER-encoded INTEGER modulus).
_PUBKEY_B64 = "AihRvNoIbTn85FZRYNZRcT+i6KpU+maCsEqr3Q5q+LDB5tH7Tz2qQ38V"
_PUBKEY_EXP = 0x10001


def _load_modulus() -> int:
    der = base64.b64decode(_PUBKEY_B64)
    assert der[0] == 0x02, "unexpected public key encoding"
    length = der[1]
    return int.from_bytes(der[2:2 + length], "big")


_MODULUS = _load_modulus()


def _decrypt_key_source(key_source: bytes) -> bytes:
    """Turn the 80-byte key source into the 56-byte Blowfish key."""
    out_len = (_MODULUS.bit_length() - 1) // 8
    out = bytearray()
    for i in range(0, len(key_source), 40):
        n = int.from_bytes(key_source[i:i + 40], "little")
        out += pow(n, _PUBKEY_EXP, _MODULUS).to_bytes(out_len, "little")
    return bytes(out[:56])


def _bf_decrypt(cipher, data: bytes) -> bytes:
    return cipher.decrypt(data)


def mix_id(name: str) -> int:
    """File-name hash used by RA2/YR MIX indices (signed 32-bit)."""
    s = name.upper().encode("latin-1")
    length = len(s)
    rem = length & 3
    if rem:
        a = length - rem
        s += bytes([rem])
        s += s[a:a + 1] * (3 - rem)
    crc = binascii.crc32(s) & 0xFFFFFFFF
    return crc - (1 << 32) if crc & 0x80000000 else crc


class MixFile:
    """A MIX archive backed by an in-memory byte buffer."""

    def __init__(self, data: bytes, name: str = "<memory>"):
        self.name = name
        self.data = data
        self.entries: dict[int, tuple[int, int]] = {}  # id -> (offset, size)
        self._parse()

    @classmethod
    def open(cls, path: str | Path) -> "MixFile":
        path = Path(path)
        return cls(path.read_bytes(), path.name)

    def _parse(self) -> None:
        data = self.data
        (first,) = struct.unpack_from("<I", data, 0)
        if first & 0xFFFF:
            # Old TD/RA1 format: no flags, header starts immediately.
            self._parse_index(data, 0)
            return
        if first & FLAG_ENCRYPTED:
            key = _decrypt_key_source(data[4:84])
            cipher = Blowfish.new(key, Blowfish.MODE_ECB)
            first_block = _bf_decrypt(cipher, data[84:92])
            count, _body = struct.unpack_from("<HI", first_block, 0)
            header_len = 6 + count * 12
            padded = (header_len + 7) & ~7
            header = _bf_decrypt(cipher, data[84:84 + padded])
            self.body_offset = 84 + padded
            self._read_entries(header, count)
        else:
            self._parse_index(data, 4)

    def _parse_index(self, data: bytes, pos: int) -> None:
        count, _body = struct.unpack_from("<HI", data, pos)
        self.body_offset = pos + 6 + count * 12
        self._read_entries(data[pos:pos + 6 + count * 12], count)

    def _read_entries(self, header: bytes, count: int) -> None:
        for i in range(count):
            eid, off, size = struct.unpack_from("<iII", header, 6 + i * 12)
            self.entries[eid] = (off, size)

    def __contains__(self, name: str) -> bool:
        return mix_id(name) in self.entries

    def read(self, name: str) -> bytes | None:
        entry = self.entries.get(mix_id(name))
        if entry is None:
            return None
        off, size = entry
        start = self.body_offset + off
        return self.data[start:start + size]

    def nested(self, name: str) -> "MixFile | None":
        raw = self.read(name)
        return MixFile(raw, name) if raw is not None else None


class MixLibrary:
    """Searches a set of MIX files (and nested MIXes) in priority order, like the game does."""

    # Highest priority first. expandmd01.mix carries the YR 1.001 patch files.
    YR_TOP_LEVEL = ["expandmd01.mix", "ra2md.mix", "ra2.mix", "langmd.mix", "language.mix",
                    "multimd.mix", "multi.mix"]
    NESTED = ["localmd.mix", "local.mix", "cachemd.mix", "cache.mix", "conqmd.mix", "conquer.mix",
              "genermd.mix", "generic.mix", "isogenmd.mix", "isogen.mix"]

    def __init__(self, game_dir: str | Path):
        self.game_dir = Path(game_dir)
        self.mixes: list[MixFile] = []
        for top in self.YR_TOP_LEVEL:
            path = self.game_dir / top
            if not path.exists():
                continue
            mix = MixFile.open(path)
            self.mixes.append(mix)
            for sub in self.NESTED:
                nested = mix.nested(sub)
                if nested is not None:
                    nested.name = f"{top}/{sub}"
                    self.mixes.append(nested)

    def find(self, name: str) -> tuple[str, bytes] | None:
        # Loose files in the game folder override MIX contents.
        loose = self.game_dir / name
        if loose.exists():
            return str(loose), loose.read_bytes()
        for mix in self.mixes:
            raw = mix.read(name)
            if raw is not None:
                return mix.name, raw
        return None
