"""
Check the decoder against the -4 capture. Expected: header steps 624, valid step slots
summing to 624, and the steps at 15:00-17:00 local (402, 105, 117 in the earlier binning).
Usage: python3 test_decode.py /path/to/btsnoop_hci_DW-H5600-4.log
"""
import pickle, struct, sys, tempfile, os
from dw_h5600_decode import decode

def notifications_from_btsnoop(path):
    # reuses the ATT parser from test_replay.py; returns CONVOY notifications in order
    from test_replay import att_events
    return [v for _, d, op, h, v in att_events(path) if d == "RX" and h == 0x0013]

def announcements_from_btsnoop(path):
    from test_replay import att_events
    ann = {}
    for _, d, op, h, v in att_events(path):
        if d == "RX" and h == 0x0010 and v[:2] == b"\x00\x2e" and v[2:3] != b"\xff":
            ann.setdefault(v[2], int.from_bytes(v[3:6], "little"))
    return ann

if __name__ == "__main__":
    path = sys.argv[1]
    notifs = notifications_from_btsnoop(path)
    ann = announcements_from_btsnoop(path)
    print("announced types:", [hex(t) for t in ann])
    out = os.path.join(tempfile.gettempdir(), "dw_h5600_steps.csv")
    res = decode(notifs, ann, out)
    for k, v in res.items():
        if k != "frames_located":
            print(k, v)
    print("CSV written to", out)
    # hourly check against the app's Day view (steps bars at 15:00, 16:00, 17:00 local)
    import csv
    from collections import Counter
    by = Counter()
    for r in csv.DictReader(open(out)):
        by[r["time_local"][11:13]] += int(r["steps"])
    print("steps by local hour:", dict(sorted(by.items())))
    assert res["header_steps"] == 624, "header steps mismatch"
    assert res["sum_steps"] == 624, "step sum mismatch"
    print("PASS: header steps and step sum match the app (624)")
