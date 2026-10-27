"""Formatted, screen output for a LifeLog session document (used by lifelog_download.py --display)."""
import csv
import io
from collections import Counter

from dw_h5600_decode import decode, session_inputs

STATUS_HEADER = 0x28


def _rule(title: str) -> str:
    return f"\n== {title} " + "=" * max(0, 60 - len(title))


def show_session(doc: dict, max_events: int = 40) -> None:
    print(_rule("Session"))
    print(f"  format      {doc['format']} v{doc['version']}")
    print(f"  device      {doc['device']}")
    print(f"  started     {doc['started']}")
    print(f"  ended       {doc['ended']}")
    print(f"  finished    {doc['finished']}")

    ev = doc["events"]
    counts = Counter((e["dir"], e["uuid"]) for e in ev)
    print(_rule(f"Events ({len(ev)})"))
    for (d, u), n in sorted(counts.items()):
        print(f"  {d:4} {u:10} {n}")
    print()
    print(f"  {'#':>4}  {'dir':4} {'char':10} {'handle':>6}  bytes")
    for i, e in enumerate(ev[:max_events]):
        h = "-" if e["handle"] is None else f"0x{e['handle']:04x}"
        hexs = e["hex"] if len(e["hex"]) <= 60 else e["hex"][:60] + "..."
        print(f"  {i:4}  {e['dir']:4} {e['uuid']:10} {h:>6}  {' '.join(hexs[j:j+2] for j in range(0, len(hexs), 2))}")
    if len(ev) > max_events:
        print(f"  ... {len(ev) - max_events} more events in the JSON file")

    print(_rule(f"Announcements ({len(doc['announcements'])})"))
    names = {0x00: "header", 0x50: "steps", 0x51: "energy", 0x52: "heart rate", 0x53: "sleep",
             0x56: "activity", 0x58: "breath", 0x59: "SpO2", 0x5A: "sleep data", 0x5B: "cardio load",
             0x63: "daily summary", 0x65: "current stats", 0x72: "5-min samples"}
    for a in doc["announcements"]:
        idx = a["index"]
        val = int.from_bytes(bytes.fromhex(a["value"]), "little")
        print(f"  0x{idx:02x}  {names.get(idx, '?'):16} announced {val}")

    status = [bytes.fromhex(e["hex"]) for e in ev if e["dir"] == "RX" and e["uuid"] == "STATUS"
              and bytes.fromhex(e["hex"])[:1] == bytes([STATUS_HEADER])]
    print(_rule(f"Status messages 0x28 ({len(status)})"))
    for s in status[-5:]:
        if len(s) >= 10:
            print(f"  {s.hex(' ')}   LifeLog-new flag (byte 9) = {s[9]}   byte 8 = {s[8]}")
        else:
            print(f"  {s.hex(' ')}")

    notifs, announced = session_inputs(doc)
    res = decode(notifs, announced)
    print(_rule("Decoded (partial)"))
    print(f"  header time (as stored)   {res['header_time_utc']}")
    print(f"  header steps              {res['header_steps']}")
    print(f"  header energy (raw)       {res['header_energy']}")
    print(f"  valid step slots          {res['valid_step_slots']}   sum {res['sum_steps']}")
    by_hour = Counter()
    for r in res["rows"]:
        by_hour[r["time_local"][11:13]] += int(r["steps"])
    if by_hour:
        print("  steps by local hour       " + ", ".join(f"{h}:00={v}" for h, v in sorted(by_hour.items())))
    print("  energy and heart rate: not decoded in this view (see README)")
    print()
