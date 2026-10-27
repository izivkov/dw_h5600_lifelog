"""
DW-H5600 LifeLog decoder (partial). Works from the raw CONVOY notifications saved by
lifelog_download.py (convoy_raw.bin / events.jsonl) and writes a CSV.

Status of each field:
  header time, header steps, header energy     confirmed (header steps 624 matched app; energy ~1263 vs app 1265)
  0x50 steps per 5-minute slot                  confirmed (valid slots sum to 624 = app total; bins match app hourly shape)
  0x51 energy per 5-minute slot                 raw value only; kcal scale NOT confirmed (/128 is a hypothesis)
  0x52 heart rate, 0x53 sleep, 0x56 activity,
  0x58-0x5b, 0x63 daily summary, 0x72           NOT decoded yet (boundaries located, fields unknown)

Format facts used here:
  * Every notification ends with a 2-byte checksum. Strip it.
  * Bulk notifications start with 0x05; control notifications are not part of the stream.
  * The whole stream is the LifeLog byte stream XOR 0xFF.
  * Frames start with type(1) + length(4, LE). Announced lengths are buffer sizes; actual
    frame ends are found by the next frame header, not by the announced length.
  * Step/energy frames are newest first: slot 0 is the header time, slot i is 5*i min earlier.
  * Records are 4 bytes: uint16 value + uint16 flag. 0xFFFE/0xFFFF(and 0xFEFE-type) mean no data.
"""
import csv
import datetime as dt
import struct
import sys
from pathlib import Path

FRAME_ORDER = [0x65, 0x50, 0x51, 0x52, 0x53, 0x56, 0x58, 0x59, 0x5A, 0x5B, 0x63, 0x72]
HEADER_MAGIC = b"\x00\x0f\x00\x00\x00"
NO_DATA = {0xFFFE, 0xFFFF}
# No-data markers vary (0xFFFE, 0xFEFE, 0xFEFF, ...). A 5-minute slot cannot hold this many
# steps, so anything above these limits is treated as no data. Limits are from observed data.
MAX_STEPS_PER_SLOT = 1000
MAX_ENERGY_RAW_PER_SLOT = 5000
TZ_OFFSET_H = -4   # Toronto EDT in late October; confirm the watch's clock convention


def stream_from_notifications(notifs):
    """notifs: list of bytes exactly as received on the CONVOY characteristic."""
    out = bytearray()
    for n in notifs:
        if n[:1] == b"\x05":
            out += n[1:-2]          # drop 0x05 prefix and 2-byte checksum
    return bytes(b ^ 0xFF for b in out)


def parse_header(s):
    if s[:5] != HEADER_MAGIC:
        raise ValueError("LifeLog header not found")
    y, mo, d, h, mi = s[5], s[6], s[7], s[8], s[9]
    t_utc = dt.datetime(2000 + y, mo, d, h, mi)
    steps = struct.unpack_from("<i", s, 14)[0]
    energy = struct.unpack_from("<H", s, 18)[0]
    return t_utc, steps, energy


def locate_frames(s, announced):
    """announced: dict type -> announced length (from BLE index announcements).
    Returns dict type -> (start, end) where the frame's 5-byte header starts at start."""
    spans, pos = {}, 20
    for t in FRAME_ORDER:
        if t not in announced:
            continue
        hdr = bytes([t]) + struct.pack("<i", announced[t])
        p = s.find(hdr, pos)
        if p < 0:
            continue
        spans[t] = p
        pos = p + 5
    # end of each frame = start of the next located frame
    order = sorted(spans.items(), key=lambda kv: kv[1])
    ends = {}
    for i, (t, p) in enumerate(order):
        ends[t] = order[i + 1][1] if i + 1 < len(order) else len(s)
    return {t: (spans[t], ends[t]) for t in spans}


def records4(s, start, end):
    body = s[start + 5:end]
    return [struct.unpack_from("<HH", body, i) for i in range(0, len(body) - 3, 4)]


def session_inputs(doc):
    """Take a session document (from lifelog_download.py) and return (notifications, announcements)."""
    notifs = [bytes.fromhex(e["hex"]) for e in doc["events"]
              if e["dir"] == "RX" and e["uuid"] == "CONVOY"]
    announced = {}
    for a in doc["announcements"]:
        announced.setdefault(a["index"], int.from_bytes(bytes.fromhex(a["value"]), "little"))
    return notifs, announced


def decode(notifs, announced, out_csv=None):
    s = stream_from_notifications(notifs)
    t_utc, header_steps, header_energy = parse_header(s)
    spans = locate_frames(s, announced)
    if 0x50 not in spans or 0x51 not in spans:
        raise ValueError("step/energy frames not found")
    steps = records4(s, *spans[0x50])
    energy = records4(s, *spans[0x51])
    n = min(len(steps), len(energy))
    rows = []
    for i in range(n):
        st, sf = steps[i]
        en, ef = energy[i]
        if st > MAX_STEPS_PER_SLOT:
            continue
        t_local = t_utc + dt.timedelta(hours=TZ_OFFSET_H) - dt.timedelta(minutes=5 * i)
        rows.append({
            "slot": i,
            "time_local": t_local.isoformat(timespec="minutes"),
            "steps": st,
            "energy_raw": en if en <= MAX_ENERGY_RAW_PER_SLOT else "",
            "energy_kcal_guess_raw_div_128": round(en / 128, 2) if en <= MAX_ENERGY_RAW_PER_SLOT else "",
            "steps_flag": hex(sf),
        })
    if out_csv:
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["slot"])
            w.writeheader()
            w.writerows(rows)
    return {
        "rows": rows,
        "header_time_utc": t_utc.isoformat(timespec="minutes"),
        "header_steps": header_steps,
        "header_energy": header_energy,
        "valid_step_slots": len(rows),
        "sum_steps": sum(r["steps"] for r in rows),
        "frames_located": {hex(t): spans[t] for t in spans},
    }


if __name__ == "__main__":
    import json
    if len(sys.argv) != 2:
        sys.exit("usage: python3 dw_h5600_decode.py <session.json>")
    doc = json.loads(Path(sys.argv[1]).read_text())
    notifs, announced = session_inputs(doc)
    res = decode(notifs, announced)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=2))
