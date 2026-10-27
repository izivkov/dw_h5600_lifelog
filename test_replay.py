"""
Offline test: replay the complete -3 transfer (rows 427..536 of the ATT event list) into
LifeLogSync and check it issues the same writes, in the same order, as the watch session did.

Usage: python3 test_replay.py /path/to/btsnoop_hci_DW-H5600-3.log
"""
import struct
import sys

from lifelog_sync import LifeLogSync, UUID_CONVOY, UUID_DRSP

HANDLE_TO_UUID = {0x0010: UUID_DRSP, 0x0013: UUID_CONVOY}
UUID_TO_HANDLE = {UUID_DRSP: 0x0010, UUID_CONVOY: 0x0013}
POST_END_HANDLE = 0x0016          # "4f" write after "03 01"
ATT_OPS = (0x12, 0x52, 0x1B, 0x1D)
WINDOW = (427, 536)               # poll ... END echo, same numbering as the earlier analysis


def att_events(path):
    raw = open(path, "rb").read()
    assert raw[:8] == b"btsnoop\x00"
    off, idx = 16, -1
    while off + 24 <= len(raw):
        olen, ilen, flags, drops, ts = struct.unpack(">IIIIq", raw[off:off + 24])
        off += 24
        pkt = raw[off:off + ilen]
        off += ilen
        if not pkt or pkt[0] != 0x02 or len(pkt) < 5:
            continue
        l2len = struct.unpack("<H", pkt[3:5])[0]
        pl = pkt[5:5 + l2len]
        if len(pl) < 5 or struct.unpack("<H", pl[2:4])[0] != 4:
            continue
        att = pl[4:]
        if att[0] not in ATT_OPS:
            continue
        idx += 1
        direction = "TX" if (flags & 1) == 0 else "RX"
        handle = struct.unpack("<H", att[1:3])[0]
        yield idx, direction, att[0], handle, att[3:]


def main(path):
    events = list(att_events(path))
    window = [e for e in events if WINDOW[0] <= e[0] <= WINDOW[1]]
    captured = [(h, v) for _, d, op, h, v in window if d == "TX"]

    sync = LifeLogSync()
    produced = []
    for w in sync.start():
        produced.append((UUID_TO_HANDLE[UUID_CONVOY], w.data))
    for _, d, op, h, v in window:
        if d != "RX" or h not in HANDLE_TO_UUID:
            continue
        for w in sync.on_notify(HANDLE_TO_UUID[h], v):
            handle = {"drsp": 0x0010, "convoy": 0x0013, "post_end": POST_END_HANDLE}[w.which]
            produced.append((handle, w.data))

    print(f"captured writes in window: {len(captured)}   produced: {len(produced)}")
    n = min(len(captured), len(produced))
    for i in range(n):
        if captured[i] != produced[i]:
            print(f"MISMATCH at write {i}")
            print(f"  captured h={captured[i][0]:#06x} {captured[i][1].hex(' ')}")
            print(f"  produced h={produced[i][0]:#06x} {produced[i][1].hex(' ')}")
            return False
    print("OK: every captured write reproduced in order")
    print("indexes announced:", [(hex(i), l) for i, l in sync.state.indexes])
    print("finished:", sync.state.done)
    return True


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1]) else 1)
