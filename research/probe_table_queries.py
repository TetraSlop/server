"""Inspect original Rule and CheckJoin consumers without a native application.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
Prints results only; all table, player, scene and message state is synthetic.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys


def probe(research_dir):
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from table_admission_vectors import AdmissionVM, SERVICE
    from unicorn.x86_const import (
        UC_X86_REG_ECX,
        UC_X86_REG_EDI,
        UC_X86_REG_ESI,
        UC_X86_REG_ESP,
    )

    rules = []
    for current in (0, SERVICE):
        machine = AdmissionVM()
        machine.w64(0x102B61D0, current)
        machine.w32(machine.child + 0x64, 0x03000C8B)
        machine.hooks[0x100120F0] = lambda sp: machine.ret(sp, cleanup=4)
        machine.hooks[0x10014EE0] = lambda sp: machine.ret(sp, cleanup=8)
        machine.hooks[0x10195190] = lambda sp: machine.ret(sp)
        stop = machine.branch(0x1004CA54, [0x1004DB95, 0x1006BAD0])
        if not current:
            assert stop == "0x1004db95" and not machine.sends
            rules.append(dict(current_table=0, query_sent=False))
            continue
        assert stop == "0x1006bad0"
        sp = machine.u.reg_read(UC_X86_REG_ESP)
        child = machine.u.reg_read(UC_X86_REG_ECX)
        target = machine.r64(sp + 4)
        assert target == current
        machine.hooks.pop(0x1006BAD0)
        machine.hooks[0x100A8FE0] = lambda sp: machine.ret(sp)
        machine.hooks[0x1019D150] = lambda sp: machine.ret(sp, 100)
        machine.call(0x1006BAD0, [target & 0xFFFFFFFF, target >> 32], this=child)
        machine.branch(
            0x1006BBB5,
            [0x1006BD54],
            {UC_X86_REG_ESI: child, UC_X86_REG_EDI: 0},
        )
        request = machine.sends[-1]
        assert request["payload"] == "12000000@Rule="
        assert request["target"] == [target & 0xFFFFFFFF, target >> 32]
        body = (
            "@Tet=/bm=25/du=0/st=1/cb=0/ca=1/gs=2/tl=4"
            "@Tab=/in=0/lu=99999/ll=0/au=300/al=100/co=8/pa=0"
        )
        machine.queue("13000000" + body)
        assert machine.call(0x10086E90, this=machine.controller) == 1
        machine.branch(
            0x1006BC07,
            [0x1006BD54],
            {UC_X86_REG_ESI: child, UC_X86_REG_EDI: 0},
        )
        game = list(struct.unpack("<I6B2x", machine.u.mem_read(0x102B5F04, 12)))
        assert game == [25, 0, 1, 0, 1, 2, 4]
        rules.append(
            dict(
                current_table=f"{current:016X}",
                query_sent=True,
                request="GTM0G" + request["payload"],
                target=f"{target:016X}",
                response="GTM0G13000000" + body,
                exported_game=dict(zip(("bm", "du", "st", "cb", "ca", "gs", "tl"), game)),
            )
        )

    machine = AdmissionVM()
    machine.call(0x100864E0, [SERVICE & 0xFFFFFFFF, SERVICE >> 32], this=machine.controller)
    check_request = machine.sends[-1]
    assert check_request["payload"] == "2D000000@CheckJoinTable="
    assert check_request["target"] == [SERVICE & 0xFFFFFFFF, SERVICE >> 32]
    flags = []
    for join, member in ((0, 0), (0, 1), (1, 0), (1, 1)):
        machine = AdmissionVM()
        machine.w64(0x102B2958, SERVICE)
        reply = f"2E000000@CheckJoinTable_Ans=/Join={join}/Mem={member}"
        machine.queue(reply)
        machine.branch(0x10093090, [0x10093137], {UC_X86_REG_ESI: machine.child})
        packed = machine.r32(0x200FF018)
        state = machine.r32(machine.child + 0x188)
        assert packed == join | (member << 1)
        assert state == (200 if packed else 202)
        if state == 200:
            # The state200 allocation uses vtable10205FCC, whose update method
            # is100922B0. Execute its first step on a synthetic child, without
            # running the later room/channel transition or any graphics.
            cleanup = 0x20370000
            machine.hooks[0x101951D0] = lambda sp: machine.ret(sp)
            machine.call(0x100922B0, this=cleanup)
            assert machine.r32(cleanup + 0x178) == 1
            assert machine.sends[-1]["payload"] == "20000000@ExitCh="
        flags.append(
            dict(
                reply="GTM0G" + reply,
                packed=packed,
                recovery_state=state,
                cleanup_messages=["GTM0G" + message["payload"] for message in machine.sends],
            )
        )
    return dict(
        code_image_sha256=hashlib.sha256(
            (research_dir / "TM_emulated_mapped.dll").read_bytes()
        ).hexdigest(),
        boundaries=(
            "Original isolated x86 instructions; synthetic messages and current-table state. "
            "Heap, diagnostics, generic widget construction, dialog reset, text measurement, "
            "and final transport are substituted. No rendering, native process or network. "
            "Recovery cleanup's first child step is seeded after verifying the original vtable."
        ),
        rule_cases=rules,
        check_request="GTM0G" + check_request["payload"],
        check_reply_cases=flags,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.research_dir), indent=2))


if __name__ == "__main__":
    main()
