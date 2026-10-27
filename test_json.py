"""
Offline end-to-end test of the JSON path, no BLE needed.
Replays a capture (btsnoop -4) through LifeLogSync, records every event with the same
Session class the runner uses, reloads the JSON, runs the display and checks the decode.

Usage: python3 test_json.py btsnoop_hci_DW-H5600-4.log
"""
import json
import sys
import tempfile
from pathlib import Path

from dw_h5600_decode import decode, session_inputs
from display import show_session
from lifelog_download import Session
from lifelog_sync import UUID_CONVOY, UUID_DRSP, UUID_STATUS, LifeLogSync
from test_replay import att_events

HANDLE_TO_UUID = {0x0010: UUID_DRSP, 0x0013: UUID_CONVOY, 0x0018: UUID_STATUS}
NAME = {UUID_DRSP: "DRSP", UUID_CONVOY: "CONVOY", UUID_STATUS: "STATUS"}


def main(path: str) -> int:
    out = Path(tempfile.mkdtemp())
    session = Session(out, {"address": "replay", "source": Path(path).name})
    sync = LifeLogSync()
    # first START on the first poll, then every notification in the capture, in order
    for w in sync.start():
        session.log("TX", "CONVOY", 0x0013, w.data)
    for _, d, op, h, v in att_events(path):
        if d != "RX" or h not in HANDLE_TO_UUID:
            continue
        uuid = HANDLE_TO_UUID[h]
        session.log("RX", NAME[uuid], None, v)
        if uuid == UUID_DRSP and v[:2] == b"\x00\x2e" and v[2:3] != b"\xff":
            session.announce(v[2], v[3:6])
        for w in sync.on_notify(uuid, v):
            session.log("TX", NAME.get(uuid, uuid), None, w.data)
        if sync.state.done:
            break
    session.close(finished=sync.state.done)
    doc = json.loads(session.path.read_text())
    print("JSON written:", session.path, f"({session.path.stat().st_size} bytes)")
    print("events:", len(doc["events"]), " announcements:", len(doc["announcements"]), " finished:", doc["finished"])

    show_session(doc, max_events=10)

    notifs, announced = session_inputs(doc)
    res = decode(notifs, announced)
    ok = res["header_steps"] == 624 and res["sum_steps"] == 624 and doc["finished"]
    print("PASS" if ok else "FAIL", "- header steps 624, step sum 624, session finished")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
