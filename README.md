# DW-H5600 LifeLog download and decode (test tools)

Status: the sync protocol is checked offline against a capture (test_replay.py).
The download has not been run against a live watch. The decoder is partial: see
"What is decoded" below.

## Files

- `lifelog_sync.py`: protocol state machine (no BLE code).
- `lifelog_download.py`: bleak runner. Saves ALL input as one JSON file.
- `dw_h5600_decode.py`: decoder. Reads a session JSON, or a list of notifications.
- `display.py`: formatted screen output for a session (used by `--display`).
- `test_replay.py`: replays a btsnoop capture through the state machine.
- `test_decode.py`: checks the decoder against a btsnoop capture.
- `test_json.py`: offline end-to-end test: capture -> JSON -> display -> decode.

## Download

    pip install bleak
    python3 lifelog_download.py --name DW-H5600 --out sessions/
    python3 lifelog_download.py --name DW-H5600 --out sessions/ --display

Each run writes `sessions/lifelog_<timestamp>.json`. The file contains:

- `device`, `started`, `ended`, `finished`
- `events`: every notification received (RX) and write sent (TX), with the characteristic,
  the handle, and the raw bytes in hex
- `announcements`: the index announcements from the watch (index and 3-byte value)

The JSON is rewritten every 20 events, so a partial session is kept if the transfer fails.
Nothing in the JSON is decoded or edited. It is the complete input for later analysis.

## Decode

    python3 dw_h5600_decode.py sessions/lifelog_<timestamp>.json

## Offline tests (no watch, no bleak needed)

    python3 test_replay.py btsnoop_hci_DW-H5600-3.log
    python3 test_json.py btsnoop_hci_DW-H5600-4.log
    python3 test_decode.py btsnoop_hci_DW-H5600-4.log

## What is decoded

Confirmed on the -4 capture:
- Header time (as stored), steps (624), and energy (1263, raw).
- Step slots: sum 624, binned by local hour 15:00 = 402, 16:00 = 105, 17:00 = 117.

Not confirmed:
- Energy scale. The `energy_kcal_guess_raw_div_128` column is a hypothesis.
- Time zone. The header is stored in UTC (22:18 = 18:18 Toronto). TZ_OFFSET_H = -4 is an assumption.
- No-data markers. Values above MAX_STEPS_PER_SLOT (1000) or MAX_ENERGY_RAW_PER_SLOT (5000)
  are treated as no data. The limits come from observed data.

Not decoded yet: heart rate (0x52), sleep (0x53), activity (0x56), 0x58-0x5b,
daily summary (0x63), 0x72.
