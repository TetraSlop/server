"""Inspect original Continue eligibility and exact sender bytes in isolation.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
Prints results only; player, collection, scene and transport state are synthetic.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
PLAYER = 0x102B63E0


def probe(research_dir):
    image = research_dir / "TM_emulated_mapped.dll"
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    assert digest == CODE_SHA256, digest
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from table_admission_vectors import AdmissionVM
    from unicorn.x86_const import UC_X86_REG_EDI

    cases = [
        dict(name="human_four_cards", mode=2, cards=4, money=1000),
        dict(name="human_money_below_double", mode=2, money=399),
        dict(name="human_money_equal_double", mode=2, money=400),
        dict(name="human_both_short", mode=2, cards=4, money=399),
        dict(name="human_money_below_cap", mode=2, money=1999, stake=1500),
        dict(name="human_money_equal_cap", mode=2, money=2000, stake=1500),
        dict(name="human_no_double", mode=2, money=200, double=0),
        dict(name="human_free_four_cards", mode=2, cards=4, money=0, stake=0),
        dict(name="human_free_five_cards", mode=2, money=0, stake=0),
        dict(name="computer_same_money_short", mode=1, money=0),
        dict(name="computer_same_money_equal", mode=1, money=400),
        dict(name="computer_change_money_short", mode=1, conf=1, money=0),
        dict(name="computer_change_four_cards", mode=1, conf=1, cards=4, money=1000),
        dict(name="computer_change_both_short", mode=1, conf=1, cards=4, money=0),
    ]
    rows = []
    for changes in cases:
        case = dict(mode=2, cards=5, money=400, stake=200, double=1, conf=0) | changes
        machine = AdmissionVM()
        machine.w8(PLAYER + 0xEA, case["mode"])
        machine.w32(PLAYER + 0xC8, case["money"])
        machine.w32(PLAYER + 0xDC, case["stake"])
        machine.w8(PLAYER + 0xE4, case["double"])
        machine.w8(machine.child + 0x188, 3)  # No button rendering in this probe.
        machine.w32(machine.game + 0x14C, 0)
        # Execute the actual collection counter100B8B30. It counts WORD card
        # types at entry+6 below the catalog limit, across0x406 sixteen-byte slots.
        machine.w32(0x102FF4FC, 100)
        collection = bytearray(0x406 * 16)
        for index in range(0x406):
            struct.pack_into(
                "<H", collection, index * 16 + 6, 0 if index < case["cards"] else 0xFFFF
            )
        machine.u.mem_write(0x102B6F10, bytes(collection))
        dialogs = []
        machine.w32(0x102FF524, 0x20400000)

        def show_dialog(sp):
            args = [machine.r32(sp + 4 + index * 4) for index in range(6)]
            assert args[0] == 2 and args[2:] == [1, 0, 1, 5], args
            dialogs.append((args[1] - 0x20400000) // 12)
            machine.ret(sp, cleanup=24)

        machine.hooks[0x1019CB60] = show_dialog
        machine.hooks[0x101814C0] = lambda sp: machine.ret(sp, cleanup=4)
        constructor = (0x1010BC4B, 0x1010BC77) if case["mode"] > 1 else (0x1010BF5C, 0x1010BF88)
        machine.branch(constructor[0], [constructor[1]], {UC_X86_REG_EDI: 0})
        next_stake = machine.r32(PLAYER + 0xDC)
        assert next_stake == min(2000, case["stake"] * (1 + case["double"]))
        machine.branch(
            0x1010D2AB if case["conf"] else 0x1010D191,
            [0x1010D21A, 0x1010D298, 0x1010D315, 0x1010D393],
            {UC_X86_REG_EDI: 0},
        )
        eligible = case["cards"] >= 5 and (case["conf"] or case["money"] >= next_stake)
        packets = ["GTM0G" + request["payload"] for request in machine.sends]
        expected = f"GTM0G43001100@Continue=/Ans=1/PR=0/Conf={case['conf']}"
        assert packets == ([expected] if eligible else []), (case, packets)
        assert machine.r16(PLAYER + 0xEC) == case["cards"]
        assert machine.r32(PLAYER + 0xC8) == case["money"]  # No debit at the choice.
        state = machine.r8(machine.child + 0x184)
        assert state == ((5 if case["conf"] else 6) if eligible else (3 if case["conf"] else 2))
        error = 0 if eligible else (2 if case["cards"] < 5 else 0)
        if not case["conf"] and case["money"] < next_stake:
            error += 1
        assert dialogs == ([machine.r8(0x10234D33 + error)] if error else [])
        rows.append(
            dict(
                **case, next_stake=next_stake, state=state, packets=packets, dialog_indices=dialogs
            )
        )

    # Explicit leave has a different sender input; eligibility failures above
    # never reach it. Stop before subsequent scene allocation/cleanup.
    machine = AdmissionVM()
    machine.branch(0x1010D00B, [0x1010D021], {UC_X86_REG_EDI: 0})
    leave = ["GTM0G" + request["payload"] for request in machine.sends]
    assert leave == ["GTM0G43001100@Continue=/Ans=2/PR=0/Conf=0"]
    return dict(
        code_image_sha256=digest,
        boundaries=[
            "Original constructor wager arithmetic, owned-card counter, choice gates and sender execute.",
            "Synthetic player/collection; dialogs, audio and transport are intercepted; no native application.",
            "UI constructor rendering, full prize flow and subsequent scene transitions are outside this probe.",
            "These client predicates do not establish a historical server rejection or recovery policy.",
        ],
        cases=rows,
        explicit_leave=leave,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.research_dir), indent=2))


if __name__ == "__main__":
    main()
