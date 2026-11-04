"""Bridge client against a fake line server speaking the ra2bridge protocol."""
import socket
import threading

import pytest

from ra2tools.bridge import Bridge, BridgeError

REPLIES = {
    "PING": "OK frame=10 mode=skirmish",
    "PRODUCE NAPOWR": "OK produce NAPOWR",
    "PLACE NAPOWR": "OK place NAPOWR 49 94",
    "DEPLOY 0x146424e8": "OK deploy 0x146424e8",
    "PRODUCE APOC": "ERR cannot build APOC now (CanBuild=0)",
}


@pytest.fixture()
def server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    seen = []

    def serve():
        conn, _ = listener.accept()
        with conn, conn.makefile("rw", newline="\n") as f:
            for line in f:
                seen.append(line.strip())
                f.write(REPLIES.get(line.strip(), "ERR unknown command") + "\n")
                f.flush()

    threading.Thread(target=serve, daemon=True).start()
    yield listener.getsockname()[1], seen
    listener.close()


def test_commands_round_trip(server):
    port, seen = server
    with Bridge(port=port) as b:
        assert b.ping() == "frame=10 mode=skirmish"
        assert b.produce("NAPOWR") == "produce NAPOWR"
        assert b.place("NAPOWR") == (49, 94)
        assert b.deploy(0x146424E8) == "deploy 0x146424e8"
        with pytest.raises(BridgeError, match="cannot build APOC"):
            b.produce("APOC")
    assert seen[3] == "DEPLOY 0x146424e8"  # addresses are sent as 8-digit hex
