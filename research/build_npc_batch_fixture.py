"""Freeze isolated original pool construction and forced hand selection.

Requires the existing external protocol_map/economy_generation_vectors.py harness,
decoded catalogue files and original memory-layout image. No native process/UI.
Run with a Python environment containing Unicorn and the harness dependencies.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    (57, (1,), 0),
    (12345, (30,), 3),
    (57, (1, 30), 0),
    (1, (1, 3), 0),
    (12345, (30, 1), 0),
    (0xFFFFFFFF, (3, 30), 1),
    (0, (14, 27), 2),
    (713, (30, 3), 3),
)


def build(research_dir):
    sys.path.insert(0, str(research_dir / "protocol_map"))
    sys.path.insert(0, str(ROOT / "src"))
    from economy_generation_vectors import Generator
    from unicorn.x86_const import (
        UC_X86_REG_EAX,
        UC_X86_REG_EIP,
        UC_X86_REG_ESI,
        UC_X86_REG_ESP,
    )
    from tmserver.economy_service import Catalog
    from tmserver.native_opponents import prepare_opponents
    from tmserver.native_rules import CRTLCG

    class Stream:
        def __init__(self, seed):
            self.rng = CRTLCG(seed)
            self.used = []

        def next(self):
            value = self.rng()
            self.used.append(value)
            return value

    class NPCMachine(Generator):
        def hook(self, machine, address, size, data):
            if address == 0x100B8550:  # Art lookup only; card import stays original.
                stack = machine.reg_read(UC_X86_REG_ESP)
                machine.reg_write(UC_X86_REG_EAX, 0)
                machine.reg_write(UC_X86_REG_EIP, self.rd(stack))
                machine.reg_write(UC_X86_REG_ESP, stack + 8)
            else:
                super().hook(machine, address, size, data)

    def compact_cards(machine, pointer, stride, count):
        return [bytes(machine.u.mem_read(pointer + i * stride, 16)).hex() for i in range(count)]

    def fields(compact):
        value = bytes.fromhex(compact)
        return (struct.unpack_from("<H", value, 6)[0], *value[:6], value[14])

    image = (research_dir / "TM_emulated_mapped.dll").read_bytes()
    encoded = (research_dir / "data/PlPrm.BIN").read_bytes()
    cases = []
    catalog = Catalog()
    for seed, npc_ids, guild in CASES:
        stream = Stream(seed)
        machine = NPCMachine(stream)
        machine.u.mem_write(0x20300000, encoded)
        machine.call(0x1017B6C0, [0x20300010, 0x20400000])
        machine.wr(0x102BB630, 0x20400010)
        selector, stack = 0x20140000, 0x200FF000
        machine.u.mem_write(selector + 0x11A, bytes([len(npc_ids), 0, *npc_ids]))
        machine.u.mem_write(0x102B648F, bytes([guild]))
        machine.u.reg_write(UC_X86_REG_ESI, selector)
        machine.u.reg_write(UC_X86_REG_ESP, stack)
        machine.u.emu_start(0x100EC12D, 0x100EC1C2, count=4_000_000)
        assert machine.u.reg_read(UC_X86_REG_EIP) == 0x100EC1C2
        generation_draws = len(stream.used)
        pools = [
            compact_cards(machine, 0x102BB420 + seat * 160, 16, 10) for seat in range(len(npc_ids))
        ]

        machine.wr(0x102BB62C, 0x20160000)
        machine.u.mem_write(0x20160000, bytes([len(npc_ids) - 1]))
        machine.u.mem_write(0x102B64CA, b"\0")  # Local chooser, not network mode.
        machine.u.mem_write(0x102B64C5, bytes([len(npc_ids) + 1]))
        machine.u.mem_write(0x102B657D, b"\0")
        machine.call(0x100D9DE0, [1])  # Forced selection: no failed-frame polls.
        hands = [
            compact_cards(machine, 0x102B68F8 + seat * 0x30C + 8, 0x9C, 5)
            for seat in range(len(npc_ids))
        ]
        unused = [
            compact_cards(machine, 0x102BB420 + seat * 160, 16, 5) for seat in range(len(npc_ids))
        ]
        expected_stream = Stream(seed)
        expected = prepare_opponents(npc_ids, catalog, expected_stream.next, guild)
        for seat, prepared in enumerate(expected):
            assert tuple(map(fields, hands[seat])) == tuple(c.full() for c in prepared.hand)
            assert tuple(map(fields, unused[seat])) == tuple(c.full() for c in prepared.unused)
        assert stream.used == expected_stream.used
        cases.append(
            {
                "seed": seed,
                "npc_ids": npc_ids,
                "guild": guild,
                "generation_draws": generation_draws,
                "total_draws": len(stream.used),
                "rng_final_state": stream.rng.state,
                "rng_draws_sha256": hashlib.sha256(
                    struct.pack(f"<{len(stream.used)}H", *stream.used)
                ).hexdigest(),
                "pools_compact16": pools,
                "hands_compact16": hands,
                "unused_compact16": unused,
            }
        )
    return {
        "evidence": "Original x86 in Unicorn; heap, supplied CRT draws and art lookup hooked. No native process/UI.",
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "construction": "100EC12D..100EC1C2, complete selected-NPC loop and ten-card pools",
        "selection": "100D9DE0(1), forced local chooser across all NPC seats",
        "scope": "Isolated stages composed consecutively; no intervening scene/board/frame draws. Historical hosted scheduling unknown.",
        "compact16": "A,T,P,M,points,arrows: bytes0..5; model: WORD6; sale: DWORD8; local index: WORD12; deck: byte14; state: byte15. Portable comparisons cover every server Card field; sale/index/state are retained as original evidence only.",
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = build(args.research_dir.resolve())
    args.output.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(fixture['cases'])} original pool/hand cases: {args.output}")


if __name__ == "__main__":
    main()
