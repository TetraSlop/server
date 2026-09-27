"""Watch original Memorable Win writes during result, import and receive paths.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
Prints evidence only; no native application, network or original file writes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
SENTINEL = b"Previous Victory\0".ljust(32, b"\xa5")


def probe(research_dir):
    digest = hashlib.sha256((research_dir / "TM_emulated_mapped.dll").read_bytes()).hexdigest()
    assert digest == CODE_SHA256, digest
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from rating_history_vectors import RatingVM
    from unicorn import UC_HOOK_MEM_WRITE
    from unicorn.x86_const import UC_X86_REG_EIP

    def watch(machine):
        writes = []

        def written(u, access, address, size, value, user_data):
            writes.append(dict(pc=f"{u.reg_read(UC_X86_REG_EIP):08X}", size=size))

        machine.u.hook_add(
            UC_HOOK_MEM_WRITE,
            written,
            begin=machine.player + 0x128,
            end=machine.player + 0x147,
        )
        return writes

    def result(machine, scores, seat, online, writes):
        p, g = machine.player, machine.game
        n = len(scores)
        machine.u.mem_write(p + 0x128, SENTINEL)
        machine.u.mem_write(p + 0x8C, bytes([2] * 32))
        machine.w16(p + 0x86, 31)
        machine.w32(p + 4, 200)
        machine.w8(p + 0xEA, online)
        machine.w8(p + 0xE9, 0)
        machine.w8(p + 0x15B, 0)
        machine.w8(g + 0x197, 0)
        machine.w8(g + 0x196, sum(scores))
        machine.w32(p + 0xDC, 100)
        machine.w32(p + 0xCC, 1234)
        machine.w16(p + 0x84, 1)
        machine.w32(p + 0x80, 0)
        for local in range(n):
            server = (local + seat) % n
            machine.w8(p + 0x1A0 + local * 0x30, scores[server])
            machine.w32(p + 0x180 + local * 0x30, 300)
            machine.w8(p + 0x19B + local * 0x30, server)
            machine.w8(p + 0x1A7 + local * 0x30, 0)
        writes.clear()
        machine.call(0x100CBD80, this=g)
        assert bytes(machine.u.mem_read(p + 0x128, 32)) == SENTINEL
        assert writes == [], (scores, seat, online, writes)
        result_class = (
            2 + sum(x > scores[seat] for x in scores) - sum(x < scores[seat] for x in scores)
        )
        assert machine.r8(p + 0x1A1) == result_class
        # Positive control: offline arithmetic actually ran; online modes skip it.
        assert machine.r16(p + 0x86) == (31 if online else 32)
        expected_rating = 200 if online else (31 * 200 + 50 * (result_class + 2)) // 32
        assert machine.r32(p + 4) == expected_rating
        return dict(
            scores=scores,
            seat=seat,
            online=online,
            result_class=result_class,
            rating=expected_rating,
            reward=machine.r32(p + 0xCC),
            rn_writes=0,
        )

    offline = []
    online = []
    for n in (2, 3):
        scores_list = (
            [[a, 10 - a] for a in range(11)]
            if n == 2
            else [[a, b, 15 - a - b] for a in range(16) for b in range(16 - a)]
        )
        for seat in range(n):
            machine = RatingVM(n, seat)
            writes = watch(machine)
            offline.extend(result(machine, scores, seat, 0, writes) for scores in scores_list)
            for mode in (1, 2):
                online.append(result(machine, [7, 3] if n == 2 else [8, 4, 3], seat, mode, writes))
    assert len(offline) == 430 and len(online) == 10

    imports = []
    for name in (b"", b"Imported Victory", b"X" * 31):
        machine = RatingVM()
        machine.u.mem_write(machine.player + 0x128, SENTINEL)
        machine.u.mem_write(0x10294BD0 + 0x7D, name.ljust(32, b"\0"))
        writes = watch(machine)
        machine.block(0x101011FA, 0x1010130C, esi=machine.player)
        assert bytes(machine.u.mem_read(machine.player + 0x128, 32)) == name.ljust(32, b"\0")
        assert writes, name
        imports.append(dict(rn=name.decode(), writer_pcs=sorted({x["pc"] for x in writes})))

    received = []
    for n in (2, 3):
        for seat in range(n):
            for value in (None, b"", b"Received Victory"):
                machine = RatingVM(n, seat)
                machine.u.mem_write(machine.player + 0x128, SENTINEL)
                writes = watch(machine)
                sections = []
                for participant in range(n):
                    text = f"@No{participant}=/D=0|0|300|0|0|1|0"
                    name = value if participant == seat else b"Other Participant"
                    if name is not None:
                        text += "/RN=" + name.hex().upper()
                    sections.append(text)
                payload = f"43000D00@ResultData=/R=0|0|0|0|0|0|0|0/P={n}" + "".join(sections)
                machine.queue(payload)
                assert machine.receive(13) == 1
                expected = "Previous Victory" if value is None else value.decode()
                assert machine.text(machine.player + 0x128, 32) == expected
                if value is None:
                    assert bytes(machine.u.mem_read(machine.player + 0x128, 32)) == SENTINEL
                    assert writes == []
                else:
                    assert len(writes) == len(value) + 1
                    assert {x["pc"] for x in writes} == {"10104444"}
                received.append(
                    dict(
                        players=n, seat=seat, rn=expected, supplied=value is not None, writes=writes
                    )
                )

    return dict(
        code_image_sha256=digest,
        scope=[
            "Complete original100CBD80 result setup, all430 score/seat cases for ten/fifteen-card boards.",
            "Ten online result controls, three profile imports and fifteen ResultData receiver controls.",
            "RN memory writes watched; only shared harness presentation/audio/runtime boundaries intercepted.",
            "No claim about other untraced client paths or historical hosted Memorable Win selection.",
        ],
        case_count=len(offline) + len(online) + len(imports) + len(received),
        offline=offline,
        online=online,
        imports=imports,
        received=received,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.research_dir), indent=2))


if __name__ == "__main__":
    main()
