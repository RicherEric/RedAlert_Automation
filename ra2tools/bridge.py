"""Python side of ra2bridge.dll: inject it into gamemd.exe and send commands over localhost TCP.

The DLL runs every command on the game thread and turns player actions into the same events the
sidebar sends, so the game validates them. See tools/ra2bridge/bridge.cpp for the protocol.
"""
from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "tools" / "ra2bridge" / "out"
DLL = BUILD_DIR / "ra2bridge.dll"
INJECTOR = BUILD_DIR / "inject.exe"
PORT = 47800


class BridgeError(RuntimeError):
    pass


def inject(pid: int) -> None:
    if not DLL.exists() or not INJECTOR.exists():
        raise BridgeError("找不到 ra2bridge.dll / inject.exe，請先執行 tools\\ra2bridge\\build.bat")
    r = subprocess.run([str(INJECTOR), str(pid), str(DLL)], capture_output=True, text=True)
    if r.returncode != 0:
        raise BridgeError(f"注入失敗：{(r.stderr or r.stdout).strip()}")


class Bridge:
    def __init__(self, host: str = "127.0.0.1", port: int = PORT, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.file = self.sock.makefile("rw", encoding="ascii", newline="\n")

    @classmethod
    def connect(cls, pid: int | None = None, wait: float = 5.0) -> "Bridge":
        """Connect; if nothing is listening and `pid` is given, inject the DLL first."""
        try:
            return cls()
        except OSError:
            if pid is None:
                raise BridgeError("連不上 ra2bridge（還沒注入？）") from None
        inject(pid)
        end = time.monotonic() + wait
        while True:
            try:
                return cls()
            except OSError:
                if time.monotonic() > end:
                    raise BridgeError("注入後仍連不上 ra2bridge") from None
                time.sleep(0.2)

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> "Bridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def send(self, line: str) -> str:
        """Send one command and return the reply text after OK; raise BridgeError on ERR."""
        self.file.write(line + "\n")
        self.file.flush()
        reply = self.file.readline().strip()
        if not reply:
            raise BridgeError("連線中斷")
        status, _, rest = reply.partition(" ")
        if status != "OK":
            raise BridgeError(rest or reply)
        return rest

    # ------------------------------------------------------------------ commands
    def ping(self) -> str:
        return self.send("PING")

    def produce(self, type_id: str) -> str:
        return self.send(f"PRODUCE {type_id}")

    def abandon(self, type_id: str) -> str:
        return self.send(f"ABANDON {type_id}")

    @staticmethod
    def _unit(unit: int | str) -> str:
        """A unit is its memory address (from monitor/GameMemory) or a type ID (first owned one)."""
        return f"0x{unit:08x}" if isinstance(unit, int) else unit

    def deploy(self, unit: int | str) -> str:
        return self.send(f"DEPLOY {self._unit(unit)}")

    def move(self, unit: int | str, cell: tuple[int, int]) -> str:
        return self.send(f"MOVE {self._unit(unit)} {cell[0]} {cell[1]}")

    def attack(self, unit: int | str, target: int) -> str:
        """Attack order on an enemy object (by memory address)."""
        return self.send(f"ATTACK {self._unit(unit)} 0x{target:08x}")

    def repair(self, building: int | str) -> str:
        """Toggle repair on one of our buildings (the game stops by itself at full health)."""
        return self.send(f"REPAIR {self._unit(building)}")

    def capture(self, unit: int | str, target: int, where: str = "d") -> str:
        """Engineer capture (tech derrick, airport, ...); infantry garrisons a civilian building."""
        return self.send(f"CAPTURE {self._unit(unit)} 0x{target:08x} {where}")

    def enter(self, unit: int | str, target: int, where: str = "d") -> str:
        """Mission::Enter (transports). Not for garrisons: conscripts shot the building instead."""
        return self.send(f"ENTER {self._unit(unit)} 0x{target:08x} {where}")

    def harvest(self, unit: int | str, cell: tuple[int, int] | None = None) -> str:
        """Harvester back to work; on that ore/gem cell if given, else the game picks."""
        return self.send(f"HARVEST {self._unit(unit)}" + (f" {round(cell[0])} {round(cell[1])}" if cell else ""))

    def camera(self, cell: tuple[int, int]) -> str:
        """Centre our own view on a cell (display only; for recording videos)."""
        return self.send(f"CAMERA {round(cell[0])} {round(cell[1])}")

    def place(self, type_id: str, cell: tuple[int, int] | None = None) -> tuple[int, int]:
        """Place a finished building; returns the cell used."""
        rest = self.send(f"PLACE {type_id}" + (f" {cell[0]} {cell[1]}" if cell else ""))
        _, _, x, y = rest.split()
        return int(x), int(y)
