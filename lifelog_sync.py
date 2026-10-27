"""
DW-H5600 LifeLog sync: protocol state machine (no BLE I/O here).

Protocol as observed in btsnoop_hci_DW-H5600-3.log (complete transfer, 18:04 session).
Every write the watch expects is produced by on_notify(); the runner in
lifelog_download.py only moves bytes between BLE and this class.

Handles below are the ones seen in the -3 capture. The runner resolves the real
handles from the UUIDs at connect time and passes them in, so nothing here is
hard-coded to a particular firmware.
"""
from dataclasses import dataclass, field
from typing import Optional

# GATT characteristics (UUIDs from gshock_api casio_constants.py)
UUID_DRSP = "26eb0023-b012-49a8-b1f8-394fb2032b0f"     # "DATA_REQUEST" - index announce / finish / START / END
UUID_CONVOY = "26eb0024-b012-49a8-b1f8-394fb2032b0f"   # "CONVOY" - poll, setup, bulk 0x05 fragments, acks
UUID_STATUS = "26eb002d-b012-49a8-b1f8-394fb2032b0f"   # watch condition 0x28 (LifeLog/ActivityLog flags); replies to setup requests
UUID_SETUP_REQUEST = "26eb002e-b012-49a8-b1f8-394fb2032b0f"   # write-without-response; one-byte setup requests

# Setup requests the official app sends, in order, before its first poll (capture -2, rows 233-259).
# Each request is one byte, and the watch answers with a status notification starting with the same byte.
# This mapping of handle 0x16 to UUID 26eb002e is inferred from the library constants and is unverified.
SETUP_COMMANDS = [0x23, 0x10, 0x11, 0x3B, 0x3A, 0x26, 0x28, 0x20]

# Byte strings copied from the capture (rows noted in comments)
POLL = bytes.fromhex("000000")                          # CONVOY poll, repeated until reply is 00 00 00
SETUP_1 = bytes.fromhex("04" + "00" * 9)               # CONVOY, 10 bytes (row 429)
SETUP_2 = bytes.fromhex("0401180018000000dc05")         # CONVOY, 10 bytes, sent after first 04 00 28 reply (row 432)
SETUP_3 = bytes.fromhex("06" + "00" * 13)               # CONVOY, 14 bytes (row 434)
ACK_FRAGMENT = bytes.fromhex("07" + "00" * 14)          # CONVOY, one per 0x05 fragment (row 442)
START = bytes.fromhex("002effffffffff")                 # DRSP (row 438)
END = bytes.fromhex("042effffffffff")                   # DRSP echo of watch END (row 536)
POST_END_OK = bytes.fromhex("4f")                       # written to handle 0x16 after "03 01" (row 538)

HDR_IDX_ANNOUNCE = 0x00   # DRSP: 00 2e <idx> <len:3 LE>
HDR_IDX_FINISH = 0x07     # DRSP: 07 2e <idx> <len:3 LE>
HDR_BULK = 0x05           # CONVOY: 05 <bulk bytes>


@dataclass
class Write:
    which: str      # "convoy" or "drsp" or "post_end"
    data: bytes


@dataclass
class SyncState:
    phase: str = "idle"
    echo_06_count: int = 0
    sent_setup_2: bool = False
    indexes: list = field(default_factory=list)   # (index, announced_len) in order
    done: bool = False


class LifeLogSync:
    """Feed every notification into on_notify(); it returns the writes to perform, in order."""

    def __init__(self) -> None:
        self.state = SyncState()

    def start(self) -> list[Write]:
        self.state.phase = "polling"
        return [Write("convoy", POLL)]

    def on_notify(self, uuid: str, data: bytes) -> list[Write]:
        s = self.state
        out: list[Write] = []

        if uuid == UUID_CONVOY:
            if s.phase == "polling":
                if data == b"\x00\x00\x00":
                    s.phase = "setup"
                    out.append(Write("convoy", SETUP_1))
                else:
                    # busy (e.g. 00 01 04 / 00 01 07): the runner must wait ~3.5 s and poll again
                    out.append(Write("convoy", POLL))
            elif s.phase == "setup":
                if data[:2] == b"\x04\x00":
                    if not s.sent_setup_2:
                        s.sent_setup_2 = True
                        out.append(Write("convoy", SETUP_2))
                    else:
                        out.append(Write("convoy", SETUP_3))
                        s.phase = "setup_wait"
                elif data[:1] == b"\x04":
                    pass  # other 04-prefixed status, ignored
            elif s.phase == "setup_wait" and data[:1] == b"\x06" and data[1:2] == b"\xfa":
                s.echo_06_count += 1
                if s.echo_06_count == 1:
                    out.append(Write("convoy", data))   # echo the watch's 9-byte value verbatim
                else:
                    out.append(Write("drsp", START))
                    s.phase = "transfer"
            elif s.phase == "transfer":
                if data[:1] == bytes([HDR_BULK]):
                    out.append(Write("convoy", ACK_FRAGMENT))
                elif data[:2] == b"\x03\x01":
                    s.phase = "post_end"
                    out.append(Write("post_end", POST_END_OK))
                    out.append(Write("convoy", POLL))
            return out

        if uuid == UUID_DRSP:
            if s.phase != "transfer":
                return out
            if len(data) >= 3 and data[:2] == b"\x00\x2e":
                index = data[2]
                announced = int.from_bytes(data[3:6], "little")
                s.indexes.append((index, announced))
                out.append(Write("drsp", bytes(data)))           # echo verbatim
            elif len(data) >= 3 and data[:2] == b"\x07\x2e":
                out.append(Write("drsp", bytes(data)))           # echo verbatim
            elif len(data) >= 2 and data[:2] == b"\x04\x2e":
                s.done = True
                s.phase = "done"
                out.append(Write("drsp", bytes(data)))           # echo END
            return out

        return out   # status / other characteristics: not used by the state machine
