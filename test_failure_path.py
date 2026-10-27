"""
Checks that a session file is written even when the transfer fails at once.
Simulates a connection error without BLE hardware.

Usage: uv run test_failure_path.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

import lifelog_download as ld


async def failing_transfer(address, session, sync):
    session.log("TX", "CONVOY", 0x0013, b"\x00\x00\x00")
    raise ConnectionError("simulated: watch not in range")


def main() -> int:
    ld._transfer = failing_transfer
    out = Path(tempfile.mkdtemp())
    try:
        asyncio.run(ld.run("AA:BB:CC:DD:EE:FF", out, display=False))
    except ConnectionError:
        pass
    files = list(out.glob("lifelog_*.json"))
    if len(files) != 1:
        print("FAIL: expected one session file, found", files)
        return 1
    doc = json.loads(files[0].read_text())
    ok = doc["finished"] is False and "simulated" in doc["error"] and len(doc["events"]) == 1
    print("PASS" if ok else "FAIL", "- file written on failure with error recorded:", files[0].name)
    print("error field:", doc.get("error"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
