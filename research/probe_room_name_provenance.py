"""Trace original DE/PD character names independently of named-protocol HN.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
Runs isolated original instructions, without native applications or networking.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
ROOM = 0x31425364758697A8
INFO = 0x20160000


def probe(research_dir):
    digest = hashlib.sha256((research_dir / "TM_emulated_mapped.dll").read_bytes()).hexdigest()
    assert digest == CODE_SHA256, digest
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from control_vectors import cases, run
    from event_contract_vectors import PLAYER, EventVM
    from room_contract_vectors import CTX, INPUT, PLAYERS, Machine, table
    from unicorn.x86_const import UC_X86_REG_ESP

    class ReceiverVM(Machine):
        def hook(self, u, address, size, data):
            if address == 0x101A0790:
                self.ret()  # Diagnostic output, not the bounded copy/parser.
            else:
                super().hook(u, address, size, data)

    class PresenceVM(EventVM):
        def __init__(self, character_name, metadata):
            super().__init__()
            self.room_sends = []
            self.w(0x10294BD4, 123)
            self.w(0x10294BD8, PLAYER)
            self.w(0x10294BDC, PLAYER >> 32)
            self.put(0x10294BE0, character_name)
            self.put(0x102B52B0, b"OtherProfile")
            self.put(0x102B52C0, b"OtherHandle")
            self.u.mem_write(INFO, metadata.ljust(32, b"\0"))

        def hook(self, u, address, size, data):
            sp = u.reg_read(UC_X86_REG_ESP)
            if address == 0x1019F3A0:
                self.u.mem_write(self.d(sp + 4), bytes(8))
                self.ret(1)
            elif address == 0x1019E7E0:
                self.room_sends.append(
                    dict(target=f"{self.q(sp + 4):016X}", body=self.c(self.d(sp + 16)))
                )
                self.ret(1)
            else:
                super().hook(u, address, size, data)

    rows = []
    # These established constructor cases deliberately use LocalPlayer for CN
    # and an independent Example argument for HN. Execute their original code.
    for name, function, arguments in cases:
        if name not in ("GameEN", "GameENC", "Req"):
            continue
        result = run(name, function, arguments)
        assert "error" not in result, result
        assert "/CN=4C6F63616C506C61796572/" in result["body"]
        assert "/HN=4578616D706C65" in result["body"]
        rows.append(dict(kind="separate_CN_HN", sender=name, body=result["body"]))

    for character_name in (b"Character", b"ABCDEFGHIJKLMNO", b"ABCDEFGHIJKLMNOP"):
        for metadata in (b"", b"Metadata"):
            for token, function, extra in (
                (b"DE", 0x10087CE0, ()),
                (b"PD", 0x10087B00, ()),
                (b"PD", 0x10087BF0, (0xDEADBEEF,)),
            ):
                machine = PresenceVM(character_name, metadata)
                assert machine.call(function, 2, INFO, *extra, ROOM & 0xFFFFFFFF, ROOM >> 32) == 1
                assert len(machine.room_sends) == 1
                sent = machine.room_sends[0]
                body = sent["body"]
                assert sent["target"] == f"{ROOM:016X}"
                assert body.startswith(b"<" + token + b">\x07") and body.endswith(b"\x07")
                fields = body[5:-1].split(b"\x06")
                assert len(fields) == 11
                expected_name = character_name + (metadata if len(character_name) == 16 else b"")
                assert fields[4] == expected_name and fields[10] == metadata
                assert fields[0] == f"0x{PLAYER:016X}".encode() and fields[1] == b"123"
                assert bytes(machine.u.mem_read(0x102B3228, 16)) == character_name.ljust(16, b"\0")
                assert bytes(machine.u.mem_read(0x102B3238, 32)) == metadata.ljust(32, b"\0")
                # The actual nested PD consumer and live player array, with
                # no rewritten name bytes between send and receive.
                receiver = ReceiverVM()
                receiver.initialize(table=None)
                receiver.dd([(101, body)])
                assert receiver.d(CTX + 8) == 101
                assert receiver.q(PLAYERS) == PLAYER
                assert receiver.d(PLAYERS + 0x18) == 123
                assert bytes(receiver.u.mem_read(PLAYERS + 0x28, 16)) == character_name.ljust(
                    16, b"\0"
                )
                assert receiver.c(PLAYERS + 0x38) == metadata
                expected_errors = (
                    [dict(kind="error", code=0xFFFFDF2C)] if len(character_name) == 16 else []
                )
                assert receiver.events == expected_errors
                rows.append(
                    dict(
                        kind="room_presence",
                        sender=f"{function:08X}",
                        token=token.decode(),
                        canonical_name=character_name.decode(),
                        metadata=metadata.decode(),
                        transmitted_name=fields[4].decode(),
                        body=body.decode(),
                        receiver_errors=receiver.events,
                        unterminated_name_bleeds_into_metadata=len(expected_name) > 16,
                    )
                )

    # This is the table-cache comparison used by nested TD, not an assertion
    # about the separate IRC transport's RFC case-mapping behavior.
    machine = Machine()
    machine.initialize(table=table())
    for channel, expected in ((b"TABLE000001", 0), (b"table000001", 0xFFFFFFFF)):
        machine.put(INPUT, channel)
        actual = machine.call(0x101A2B20, CTX, INPUT)
        assert actual == expected
        rows.append(dict(kind="table_cache_channel", channel=channel.decode(), index=actual))

    return dict(
        code_image_sha256=digest,
        case_count=len(rows),
        scope=[
            "Original named CN/HN constructors and all three DE/PD record builders/serializers.",
            "Original live room-player decoder, including its unterminated16-byte-name diagnostic.",
            "No native16-byte-name safety claim: nonempty metadata demonstrably extends the sent name.",
            "Original table-cache channel comparison only; IRC normalization remains separate.",
            "Heap, locks, timestamp, host callback and final transport boundaries substituted; no native launch.",
        ],
        cases=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.research_dir), indent=2))


if __name__ == "__main__":
    main()
