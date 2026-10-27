"""
Download the DW-H5600 LifeLog from the watch over BLE and save ALL input as one JSON file.

Every notification received and every write sent is recorded, with a timestamp, the
characteristic, the handle and the raw bytes in hex. Nothing is decoded here.
The JSON is rewritten periodically during the transfer, so a partial session survives.

Requires: pip install bleak
Usage:
    python3 lifelog_download.py --name DW-H5600 --out sessions/
    python3 lifelog_download.py --address AA:BB:CC:DD:EE:FF --out sessions/
    python3 lifelog_download.py --name DW-H5600 --out sessions/ --display

Output: sessions/lifelog_<timestamp>.json, with the structure:
    {
      "format": "dw_h5600_lifelog_session", "version": 1,
      "device": {...}, "started": "...", "finished": true|false, "ended": "...",
      "events": [ {"t": ..., "dir": "RX"|"TX"|"skip", "uuid": ..., "handle": ..., "hex": ...}, ...],
      "announcements": [ {"index": 0, "value": "..."}, ... ]
    }

Not tested against a real watch. The protocol sequence is checked offline by test_replay.py.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from lifelog_sync import UUID_CONVOY, UUID_DRSP, UUID_STATUS, LifeLogSync

POLL_RETRY_S = 3.5          # the official app waits ~3.5 s between busy polls
OVERALL_TIMEOUT_S = 180
FLUSH_EVERY = 20            # rewrite the JSON after this many events
CHAR_NAMES = {UUID_DRSP: "DRSP", UUID_CONVOY: "CONVOY", UUID_STATUS: "STATUS", "post_end": "POST_END"}


class Session:
    """Collects every event of one download and writes it as a single JSON document."""

    def __init__(self, out_dir: Path, device: dict) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = out_dir / f"lifelog_{stamp}.json"
        self.doc = {
            "format": "dw_h5600_lifelog_session",
            "version": 1,
            "device": device,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "finished": False,
            "ended": None,
            "events": [],
            "announcements": [],
        }
        self._since_flush = 0
        self.save()   # create the file now, so a failure at any later point still leaves it on disk

    def log(self, direction: str, uuid: str, handle, data: bytes) -> None:
        print(f"{direction:4} {uuid:8} {data.hex(' ')[:60]}", flush=True)
        self.doc["events"].append({
            "t": round(time.time(), 3), "dir": direction,
            "uuid": uuid, "handle": handle, "hex": data.hex(),
        })
        self._since_flush += 1
        if self._since_flush >= FLUSH_EVERY:
            self.save()

    def announce(self, index: int, value: bytes) -> None:
        self.doc["announcements"].append({"index": index, "value": value.hex()})

    def save(self) -> None:
        self._since_flush = 0
        self.path.write_text(json.dumps(self.doc, indent=2))

    def close(self, finished: bool) -> None:
        self.doc["finished"] = finished
        self.doc["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.save()


async def find_device(name: str | None, address: str | None):
    from bleak import BleakScanner   # imported here so the offline tests run without bleak
    if address:
        return address
    devices = await BleakScanner.discover(timeout=10.0)
    for d in devices:
        if d.name and name and name.lower() in d.name.lower():
            return d.address
    raise SystemExit(f"no device with name containing {name!r} found")


async def run(address: str, out_dir: Path, display: bool) -> Path:
    session = Session(out_dir, {"address": address})
    sync = LifeLogSync()
    try:
        await _transfer(address, session, sync)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        session.doc["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        session.close(finished=sync.state.done)   # always written, even on errors or Ctrl-C
    print(f"saved to {session.path}")
    if display:
        from display import show_session
        show_session(json.loads(session.path.read_text()))
    return session.path


async def _transfer(address: str, session: Session, sync: LifeLogSync) -> None:
    from bleak import BleakClient   # imported here so the offline tests run without bleak
    inbox: asyncio.Queue = asyncio.Queue()

    async with BleakClient(address) as client:
        print(f"connected to {address}", flush=True)
        await client.get_services()   # explicit discovery; avoids "Service Discovery has not been performed yet"
        chars = {c.uuid: c for s in client.services for c in s.characteristics}
        print(f"found {len(chars)} characteristics", flush=True)
        for uuid in (UUID_DRSP, UUID_CONVOY, UUID_STATUS):
            if uuid not in chars:
                raise SystemExit(f"characteristic {uuid} not found; is this a DW-H5600?")

        def on_notify(char, data: bytearray) -> None:
            inbox.put_nowait((char.uuid, bytes(data)))

        for uuid in (UUID_DRSP, UUID_CONVOY, UUID_STATUS):
            await client.start_notify(chars[uuid], on_notify)

        async def write(which: str, data: bytes) -> None:
            if which == "drsp":
                char, response = chars[UUID_DRSP], True
            elif which == "convoy":
                char, response = chars[UUID_CONVOY], False
            else:
                session.log("skip", "POST_END", None, data)   # not sent: the characteristic is not identified yet
                return
            session.log("TX", CHAR_NAMES.get(char.uuid, char.uuid), char.handle, data)
            await client.write_gatt_char(char, data, response=response)

        async def do_writes(writes) -> None:
            for w in writes:
                await write(w.which, w.data)

        async def handle_one(uuid: str, data: bytes) -> None:
            session.log("RX", CHAR_NAMES.get(uuid, uuid), None, data)
            if uuid == UUID_DRSP and data[:2] == b"\x00\x2e" and data[2:3] != b"\xff":
                session.announce(data[2], data[3:6])
            await do_writes(sync.on_notify(uuid, data))

        await do_writes(sync.start())
        deadline = time.monotonic() + OVERALL_TIMEOUT_S
        while not sync.state.done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("timeout: transfer did not finish; partial data is saved")
                break
            try:
                uuid, data = await asyncio.wait_for(inbox.get(), timeout=min(remaining, POLL_RETRY_S + 5))
            except asyncio.TimeoutError:
                if sync.state.phase == "polling":
                    await do_writes(sync.on_notify(UUID_CONVOY, b"\x00\x01\x04"))   # re-poll
                continue
            await handle_one(uuid, data)

        for uuid in (UUID_DRSP, UUID_CONVOY, UUID_STATUS):
            await client.stop_notify(chars[uuid])



def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="DW-H5600")
    p.add_argument("--address")
    p.add_argument("--out", default="sessions")
    p.add_argument("--display", action="store_true", help="print the formatted session after the download")
    args = p.parse_args()
    address = asyncio.run(find_device(args.name, args.address))
    asyncio.run(run(address, Path(args.out), args.display))


if __name__ == "__main__":
    main()
