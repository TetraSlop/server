"""Original DE/PD information-string bounds and production room publications.

The external Unicorn harness executes original senders, DD decoders, PTL copy,
and bounded room-player projection. No native application or network is used.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace


CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
INFO = 0x20160000
TEMPLATE = b"LocalPlayer    LAAAABAGDBAABIAB"
COPY_ERROR = {"kind": "error", "code": 0xFFFFDF2C}  # -8404


def probe(research_dir, repo):
    assert len(TEMPLATE) == 31
    digest = hashlib.sha256((research_dir / "TM_emulated_mapped.dll").read_bytes()).hexdigest()
    assert digest == CODE_SHA256
    sys.path[:0] = [str(repo / "src"), str(research_dir / "protocol_map")]
    from event_contract_vectors import EventVM, PLAYER
    from room_contract_vectors import CTX, INPUT, PLAYERS, PTL
    from room_native_transition_vectors import Room
    from tmserver.rooms.packets import ROOM_ID, player_record
    from tmserver.rooms.service import RoomService
    from tmserver.protocol.commands import Command, encode, parse
    from unicorn import UC_HOOK_MEM_READ
    from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EIP

    def info(length):
        return (TEMPLATE + b"XY")[:length]

    def payload(metadata):
        return b"\x06".join(
            (
                b"0x0000000000000001",
                b"1",
                b"0",
                b"0",
                b"LocalPlayer",
                b"3",
                b"0",
                b"3604486",
                b"0",
                f"0x{ROOM_ID:016X}".encode(),
                metadata,
            )
        )

    class Receiver(Room):
        def hook(self, u, address, size, data):
            if address == 0x101A0790:
                self.ret()  # Diagnostic text sink; the copy/error producer runs.
            else:
                super().hook(u, address, size, data)

    class Sender(EventVM):
        def __init__(self, metadata, adjacent):
            super().__init__()
            self.sent = []
            self.w(0x10294BD4, 1)
            self.w(0x10294BD8, PLAYER)
            self.w(0x10294BDC, PLAYER >> 32)
            self.put(0x10294BE0, b"LocalPlayer")
            self.u.mem_write(INFO, metadata.ljust(32, b"\0"))
            self.put(0x102B3258, adjacent)

        def hook(self, u, address, size, data):
            sp = u.reg_read(UC_X86_REG_ESP)
            if address == 0x1019F3A0:
                self.u.mem_write(self.d(sp + 4), bytes(8))
                self.ret(1)
            elif address == 0x1019E7E0:
                self.sent.append(self.c(self.d(sp + 16)))
                self.ret(1)
            else:
                super().hook(u, address, size, data)

    def projection(vm):
        vm.call(0x10086010)
        reads = []

        def read(u, access, address, size, value, data):
            # First exported player's info is at raw10286358+38. Restrict this
            # observation to the derived projection, excluding raw PTL copies.
            begin = 0x10286390
            if begin <= address < begin + 32:
                reads.append(
                    {
                        "pc": f"{u.reg_read(UC_X86_REG_EIP):08X}",
                        "offset": address - begin,
                        "size": size,
                    }
                )

        handle = vm.u.hook_add(UC_HOOK_MEM_READ, read)
        vm.call(0x10066950)
        vm.u.hook_del(handle)
        rows = [
            {
                "identity": vm.q(p),
                "name": vm.c(p + 0x20).decode(),
                "status": vm.d(p + 0x48),
                "substatus": vm.d(p + 0x4C),
            }
            for p in range(vm.d(0x1024F120), vm.d(0x1024F124), 0x70)
        ]
        assert all(item["offset"] + item["size"] <= 28 for item in reads)
        return rows, reads

    def receive(vm, packet):
        vm.events = []
        vm.put(INPUT, bytes(0x22) + packet[5:])
        assert vm.call(0x101A2D50, INPUT + 0x22) == 0
        vm.call(0x101A07E0, INPUT)
        return list(vm.events)

    cases = []
    for length in (0, 31, 32):
        for adjacent in (b"", b"TAIL"):
            for token, function, extra in (
                (b"DE", 0x10087CE0, ()),
                (b"PD", 0x10087B00, ()),
                (b"PD", 0x10087BF0, (0xDEADBEEF,)),
            ):
                metadata = info(length)
                vm = Sender(metadata, adjacent)
                assert vm.call(function, 2, INFO, *extra, ROOM_ID & 0xFFFFFFFF, ROOM_ID >> 32) == 1
                assert len(vm.sent) == 1
                body = vm.sent[0]
                assert body.startswith(b"<" + token + b">\x07")
                fields = body[5:-1].split(b"\x06")
                expected = metadata + (adjacent if length == 32 else b"")
                assert fields[4] == b"LocalPlayer" and fields[10] == expected
                assert bytes(vm.u.mem_read(0x102B3238, 32)) == metadata.ljust(32, b"\0")
                cases.append(
                    {
                        "kind": "original_sender",
                        "function": f"{function:08X}",
                        "length": length,
                        "adjacent": adjacent.decode(),
                        "sent_info": fields[10].decode(),
                        "body_hex": body.hex(),
                    }
                )

    projections = {}
    for length in (0, 30, 31, 32, 33):
        for token in (b"DE", b"PD"):
            metadata = info(length)
            vm = Receiver()
            vm.initialize()
            body = encode([Command(token, payload(metadata))])
            vm.dd([(101, body)])
            errors = list(vm.events)
            assert errors == ([COPY_ERROR] if length >= 32 else [])
            assert vm.d(CTX + 8) == 101
            stored = bytes(vm.u.mem_read(PLAYERS + 0x38, 32))
            assert stored == metadata[:32].ljust(32, b"\0")
            rows, reads = projection(vm)
            projections[length] = rows
            assert rows[0]["name"] == ("LocalPlayer" if length else "")
            assert (rows[0]["status"], rows[0]["substatus"]) == ((3, 1) if length else (0, 0))
            cases.append(
                {
                    "kind": "original_delta",
                    "token": token.decode(),
                    "length": length,
                    "errors": errors,
                    "stored_info_hex": stored.hex(),
                    "projected": rows,
                    "info_reads": reads,
                }
            )

    for length in (0, 30, 31, 32):
        metadata = info(length)
        # Construct the full-width negative control independently of any future
        # server rejection, starting with a valid terminated production record.
        _, record = player_record(payload(metadata[:31]))
        record = bytearray(record)
        record[0x38:0x58] = metadata.ljust(32, b"\0")
        snapshot = bytearray(0xC050)
        struct.pack_into("<III", snapshot, 0x40, 100, 1, 0)
        snapshot[0x50:0xA8] = record
        vm = Receiver()
        vm.initialize()
        vm.u.mem_write(PTL, bytes(snapshot))
        vm.call(0x101A0C90, CTX, PTL)
        assert not vm.events
        rows, reads = projection(vm)
        assert rows == projections[length]
        assert bytes(vm.u.mem_read(PLAYERS, 0x58)) == bytes(record)
        cases.append(
            {
                "kind": "original_ptl",
                "length": length,
                "errors": [],
                "projected": rows,
                "info_reads": reads,
            }
        )

    unsafe = []
    for length in (0, 30, 31, 32):
        for token in (b"DE", b"PD"):
            room = RoomService()
            vm = Receiver()
            vm.initialize()
            if token == b"PD":
                admitted = room.handle(
                    "one", b"GTM0L" + encode([Command(b"DE", payload(TEMPLATE))])
                )
                receive(vm, admitted[-1][1])
            before = room.snapshot()
            try:
                deliveries = room.handle(
                    "one", b"GTM0L" + encode([Command(token, payload(info(length)))])
                )
            except ValueError as exc:
                assert length == 32 and room.snapshot() == before
                cases.append(
                    {
                        "kind": "production_presence",
                        "token": token.decode(),
                        "length": length,
                        "accepted": False,
                        "error": str(exc),
                        "unchanged": True,
                    }
                )
                continue
            errors = receive(vm, deliveries[-1][1])
            assert errors == ([COPY_ERROR] if length == 32 else [])
            if errors:
                unsafe.append(f"{token.decode()} info length{length}")
            cases.append(
                {
                    "kind": "production_presence",
                    "token": token.decode(),
                    "length": length,
                    "accepted": True,
                    "receiver_errors": errors,
                }
            )

    room = RoomService()
    vm = Receiver()
    vm.initialize()
    admitted = room.handle("one", b"GTM0L" + encode([Command(b"DE", payload(TEMPLATE))]))
    receive(vm, admitted[-1][1])
    deliveries = room.practice_opponent(
        "one", SimpleNamespace(name=b"LocalCPU", nickname_id=2, character_id=2)
    )
    published = parse(deliveries[-1][1][5:])[-1].payload.split(b"\x06")[-1]
    errors = receive(vm, deliveries[-1][1])
    assert len(published) in (31, 32)
    assert errors == ([COPY_ERROR] if len(published) == 32 else [])
    rows, _ = projection(vm)
    assert rows[1] == {"identity": 2, "name": "LocalCPU", "status": 3, "substatus": 1}
    if errors:
        unsafe.append("production CPU information length32")
    cases.append(
        {
            "kind": "production_cpu",
            "length": len(published),
            "info": published.decode(),
            "receiver_errors": errors,
            "projected": rows,
        }
    )

    return {
        "code_image_sha256": digest,
        "case_count": len(cases),
        "cases": cases,
        "unsafe_production_publications": unsafe,
        "scope": [
            "Original complete DE/PD wrappers and serializer, with synthetic adjacent-memory canaries.",
            "Original nested DD decoder, bounded info copy and live player-array update.",
            "Original PTL copier and full bounded player projection; presentation sorting bypassed.",
            "Production presence echo and CPU builder are observed without changing runtime policy.",
            "All canonical names are short and terminated; the separate full-width name issue is excluded.",
        ],
        "limits": [
            "The projection reads only info offsets0..27 in these cases; this does not exhaust every client consumer.",
            "No visible crash, unsafe rendered string, historical rejection policy or general metadata grammar is claimed.",
            "Heap, locks, timestamp, diagnostics, final transport and presentation sorting are fixture boundaries.",
            "No native application, external connection, live save or original asset writes.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, default=Path("results/room-info-boundary-original.json")
    )
    parser.add_argument("--require-safe-production", action="store_true")
    args = parser.parse_args()
    report = probe(args.research_dir.resolve(), args.repo.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"Verified {report['case_count']} original/production cases; unsafe production publications: {len(report['unsafe_production_publications'])}"
    )
    print(args.output.resolve())
    if args.require_safe_production:
        assert not report["unsafe_production_publications"], report[
            "unsafe_production_publications"
        ]


if __name__ == "__main__":
    main()
