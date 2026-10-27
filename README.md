# DW-H5600 LifeLog raw download (test program)

Status: protocol sequence verified offline against btsnoop_hci_DW-H5600-3.log
(test_replay.py: all 55 writes in the complete session reproduced in order).
Not yet run against a real watch.

Files
- lifelog_sync.py      protocol state machine (pure Python, no BLE)
- lifelog_download.py  bleak runner; saves raw bytes as they arrive
- test_replay.py       offline check against the -3 capture

Run
    pip install bleak
    python3 test_replay.py /path/to/btsnoop_hci_DW-H5600-3.log   # offline check
    python3 lifelog_download.py --name DW-H5600 --out sessions/  # real watch

Known gaps
- What the setup bytes 04 01 18 00 18 00 00 00 dc 05 and 06 00... mean is unknown; they are replayed as the capture sent them.
- The 0x28 watch-condition message is only logged, not requested (the request is not yet identified).
- The write after "03 01" goes to handle 0x0016, which the runner only uses if the watch exposes it.
- Whether the watch erases its LifeLog buffer after END is unknown. Keep the raw session
  before doing anything else with the watch.
- The stream_xor_ff.bin output relies on the XOR 0xFF hypothesis and the trailer
  handling is not finished, so decode from convoy_raw.bin, not from the derived stream.

## Decoder (partial, updated)

dw_h5600_decode.py reads the raw CONVOY notifications and writes a per-slot CSV.

Confirmed on the -4 capture (test_decode.py passes):
- Header time, steps (624), and energy (1263 kcal raw)
- Step slots: sum 624, binned by local hour 15:00 = 402, 16:00 = 105, 17:00 = 117

Not confirmed:
- Energy scale: the CSV's /128 column is a hypothesis. The regression of energy on steps suggests a resting intercept of about 726 raw per 5 minutes and about 5.1 raw per step, but the valid energy slots don't yet sum to the app's day total.
- Time zone: the header is printed in UTC (22:18 UTC = 18:18 Toronto). TZ_OFFSET_H = -4 is an assumption until the watch clock is checked.
- No-data markers: several codes (0xFFFE, 0xFEFE, 0xFEFF...) are treated as no data using plausibility limits (MAX_STEPS_PER_SLOT = 1000, MAX_ENERGY_RAW_PER_SLOT = 5000). These limits come from the data seen so far.

Not decoded yet: heart rate (0x52), sleep (0x53), activity (0x56), 0x58-0x5b, daily summary (0x63), 0x72.
