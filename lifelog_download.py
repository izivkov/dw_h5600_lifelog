"""
Download the DW-H5600 LifeLog from the watch over BLE and save the RAW bytes.

Nothing is decoded here. Every notification and write is appended to events.jsonl as it
happens, so a partial transfer is still on disk if the sync fails.

Requires: pip install bleak
Usage:
    python3 lifelog_download.py --name DW-H5600 --out sessions/
    python3 lifelog_download.py --address AA:BB:CC:DD:EE:FF --out sessions/

Output (one directory per run, timestamped):
    events.jsonl          every BLE event: time, direction, characteristic, handle, hex
    status_0x28.bin       raw watch-condition notifications (LifeLog/ActivityLog flags)
    convoy_raw.bin        every CONVOY notification exactly as received (0x05 prefix kept)
    payload_stream.bin    the same notifications with the 0x05 prefix removed
    announcements.json    index announcements (index, 3-byte value) in order
    stream_xor_ff.bin     payload_stream.bin with every byte XOR 0xFF (hypothesis)

Not tested against a real watch. The protocol sequence is checked offline by test_replay.py.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

from lifelog_sync import (ACK_FRAGMENT, LifeLogSync, UUID_CONVOY, UUID_DRSP, UUID_STATUS)

POLL_RETRY_S = 3.5          # the official app waits ~3.5 s between busy polls
POST_END_HANDLE = 0x0016    # written after "03 01"; resolved from the device's own handles
OVERALL_TIMEOUT_S = 180


class Session:
    def __init__(self, out_dir: Path) -> None:
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = open(self.dir / "events.jsonl", "a", buffering=1)
        self.status = open(self.dir / "status_0x28.bin", "ab", buffering=1)
        self.convoy_raw = open(self.dir / "convoy_raw.bin", "ab", buffering=1)
        self.payload = open(self.dir / "payload_stream.bin", "ab", buffering=1)
        self.announcements: list[dict] = []

    def log(self, direction: str, uuid: str, handle: int | None, data: bytes) -> None:
        self.events.write(json.dumps({
            "t": time.time(), "dir": direction, "uuid": uuid,
            "handle": handle, "hex": data.hex(),
        }) + "\n")

    def close(self) -> None:
        self.events.close()
        self.status.close()
        self.convoy_raw.close()
        self.payload.close()
        stream = (self.dir / "payload_stream.bin").read_bytes()
        (self.dir / "stream_xor_ff.bin").write_bytes(bytes(b ^ 0xFF for b in stream))
        (self.dir / "announcements.json").write_text(json.dumps(self.announcements, indent=1))


async def find_device(name: str | None, address: str | None):
    if address:
        return address
    devices = await BleakScanner.discover(timeout=10.0)
    for d in devices:
        if d.name and name and name.lower() in d.name.lower():
            return d.address
    raise SystemExit(f"no device with name containing {name!r} found")


async def run(address: str, out_root: Path) -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = Session(out_root / f"lifelog_{stamp}")
    sync = LifeLogSync()
    inbox: asyncio.Queue = asyncio.Queue()

    async with BleakClient(address) as client:
        chars = {c.uuid: c for s in client.services for c in s.characteristics}
        handles = {c.handle: c for s in client.services for c in s.characteristics}
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
                char = handles.get(POST_END_HANDLE)
                if char is None:
                    session.log("skip", "post_end", POST_END_HANDLE, data)
                    return
                response = False
            session.log("TX", char.uuid, char.handle, data)
            await client.write_gatt_char(char, data, response=response)

        async def do_writes(writes) -> None:
            for w in writes:
                await write(w.which, w.data)

        # watch condition: record every 0x28 notification the watch sends during the session
        async def handle_one(uuid: str, data: bytes) -> None:
            if uuid == UUID_STATUS:
                if data[:1] == b"\x28":
                    session.status.write(data)
                session.log("RX", uuid, None, data)
                return
            session.log("RX", uuid, None, data)
            if uuid == UUID_CONVOY:
                session.convoy_raw.write(data)
                if data[:1] == b"\x05":
                    session.payload.write(data[1:])
            if uuid == UUID_DRSP and data[:2] == b"\x00\x2e":
                session.announcements.append({"index": data[2], "value": data[3:6].hex()})
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

    session.close()
    print(f"saved to {session.dir}")
    print("finished" if sync.state.done else "NOT finished")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="DW-H5600")
    p.add_argument("--address")
    p.add_argument("--out", default="sessions")
    args = p.parse_args()
    address = asyncio.run(find_device(args.name, args.address))
    asyncio.run(run(address, Path(args.out)))


if __name__ == "__main__":
    main()
