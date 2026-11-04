"""Reader for Westwood CSF string tables (ra2md.csf)."""
from __future__ import annotations

import struct


def parse_csf(data: bytes) -> dict[str, str]:
    """Return {label (upper-case): text}."""
    if data[:4] != b" FSC":
        raise ValueError("not a CSF file")
    _version, num_labels = struct.unpack_from("<II", data, 4)
    pos = 24
    table: dict[str, str] = {}
    for _ in range(num_labels):
        if data[pos:pos + 4] != b" LBL":
            break
        num_pairs, name_len = struct.unpack_from("<II", data, pos + 4)
        pos += 12
        label = data[pos:pos + name_len].decode("latin-1")
        pos += name_len
        for i in range(num_pairs):
            tag = data[pos:pos + 4]
            (length,) = struct.unpack_from("<I", data, pos + 4)
            pos += 8
            raw = bytes(~b & 0xFF for b in data[pos:pos + length * 2])
            pos += length * 2
            if i == 0:
                table[label.upper()] = raw.decode("utf-16-le", errors="replace")
            if tag == b"WRTS":
                (extra,) = struct.unpack_from("<I", data, pos)
                pos += 4 + extra
    return table
