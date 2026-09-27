"""Freeze original human Join eligibility against current table metadata.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
No native application, network, original asset write or live save is involved.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]


def build(research_dir):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from room_contract_vectors import Machine, table
    from table_admission_vectors import AdmissionVM
    from tmserver.match_service import DEFAULT_GAME_RULES, DEFAULT_TABLE_RULES
    from tmserver.table_metadata import table_info
    from unicorn.x86_const import UC_X86_REG_EBP, UC_X86_REG_EDI, UC_X86_REG_ESI

    specs = [
        dict(name="occupied_baseline"),
        *(dict(name=f"owned_cards_{n}", cards=n) for n in (0, 4, 5, 6)),
        *(dict(name=f"occupancy_{n}", count=n) for n in (0, 1, 2, 3)),
        *(dict(name=f"state_{n}", state=n) for n in (1, 2, 3, 4, 5, 6)),
        *(dict(name=f"balance_{n}", balance=n) for n in (49, 50, 51)),
        *(dict(name=f"cp_{n}", cp=n) for n in (9, 10, 20, 21)),
        dict(name="cp_word_not_rating_or_tier", rating=99999, tier=255, cp_high=0xABCD),
        *(
            dict(
                name=f"empty_{'custom' if context else 'fixed'}_balance_{balance}",
                state=0,
                count=0,
                context=context,
                balance=balance,
            )
            for context in (0, 1)
            for balance in (0, 49, 50)
        ),
        dict(name="empty_custom_four_cards", state=0, count=0, context=1, cards=4),
        dict(name="pending_owner_still_state0", state=0, count=1, context=1),
    ]
    stops = {
        0x100477D0: "closed_state6",
        0x1004782A: "empty_fixed_insufficient_money",
        0x1004783F: "empty_allowed",
        0x100478BA: "insufficient_money",
        0x100478EF: "existing_allowed",
        0x10047913: "outside_cp_bounds",
        0x1004792F: "full_closed_or_fewer_than_five_cards",
    }
    rows = []
    for changes in specs:
        spec = (
            dict(
                cards=5,
                count=2,
                state=1,
                wager=50,
                balance=50,
                cp=15,
                lower=10,
                upper=20,
                context=0,
                rating=300,
                tier=1,
                cp_high=0,
            )
            | changes
        )
        game = dict(DEFAULT_GAME_RULES, bm=spec["wager"])
        rules = dict(DEFAULT_TABLE_RULES, ll=spec["lower"], lu=spec["upper"])
        metadata = table_info(1, spec["count"], int(spec["count"] > 0), game, rules)
        room = Machine()
        room.initialize(table=table(metadata, state=spec["state"]))
        room.w(0x102B54EC, spec["context"])
        room.u.mem_write(
            0x102B5504,
            struct.pack("<I6B2x", *(game[k] for k in ("bm", "du", "st", "cb", "ca", "gs", "tl"))),
        )
        room.refresh()
        derived = bytes(room.u.mem_read(room.d(0x1024F110), 0x8B0))
        native = {
            key: struct.unpack_from("<I", derived, offset)[0]
            for key, offset in (
                ("state", 0x18),
                ("room_context", 0x1C),
                ("wager", 0x20),
                ("upper", 0x30),
                ("lower", 0x34),
                ("au", 0x38),
                ("al", 0x3C),
                ("count", 0x54),
                ("flags", 0x8AC),
            )
        }
        machine = AdmissionVM()
        record = 0x20360000
        machine.u.mem_write(record, derived)
        machine.w32(machine.child + 0x168, native["wager"])
        machine.w32(0x102B64A8, spec["balance"])
        machine.w32(0x102B63E0, spec["cp"] | spec["cp_high"] << 16)
        machine.w32(0x102B63E4, spec["rating"])
        machine.w8(0x102B648D, spec["tier"])
        machine.hooks[0x100B8B30] = lambda sp, n=spec["cards"]: machine.ret(sp, n)
        stop = int(
            machine.branch(
                0x100477BC,
                list(stops),
                {UC_X86_REG_ESI: record, UC_X86_REG_EBP: machine.child, UC_X86_REG_EDI: 0},
            ),
            16,
        )
        if spec["state"] == 6:
            expected = "closed_state6"
        elif spec["state"] == 0 and spec["cards"] >= 5:
            expected = (
                "empty_fixed_insufficient_money"
                if spec["context"] == 0 and spec["balance"] < native["wager"]
                else "empty_allowed"
            )
        elif spec["count"] >= 3 or spec["cards"] < 5 or spec["state"] in (2, 3, 4, 5):
            expected = "full_closed_or_fewer_than_five_cards"
        elif spec["balance"] < native["wager"]:
            expected = "insufficient_money"
        elif not native["lower"] <= spec["cp"] <= native["upper"]:
            expected = "outside_cp_bounds"
        else:
            expected = "existing_allowed"
        assert stops[stop] == expected, (spec, native, hex(stop), expected)
        rows.append(
            dict(
                **spec,
                metadata=metadata.decode("ascii"),
                native=native,
                stop=f"{stop:08X}",
                outcome=expected,
                earlier_busy_guard=native["flags"] & 15 == 2,
            )
        )
    return dict(
        code_image_sha256=hashlib.sha256(
            (research_dir / "TM_emulated_mapped.dll").read_bytes()
        ).hexdigest(),
        original=dict(
            metadata_projection="101A0C90 -> 10086010 -> 10066D10",
            wager_copy_to_button="10045B0F..10045B12",
            join_gate="100477BC..10047981",
            empty_state_branch="100477EF..10047856",
            existing_cp_check="100478D9..10047913",
            earlier_busy_guard="100475D3..1004768A",
        ),
        boundaries=[
            "Current production metadata encoding; complete original PTL projection.",
            "Synthetic collection count replaces100B8B30; player CP, rating, tier and balance are seeded.",
            "Join predicate executes for a nonmember; stops before rendering/widget calls.",
            "Earlier busy-state override is recorded separately, not bypassed as a claim of UI availability.",
            "Client gate evidence does not identify historical server rejection codes or retry policy.",
            "Password local comparison is documented separately; no password proof exists in GameEN/Req.",
        ],
        cases=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tests/fixtures/table_eligibility.json"
    )
    args = parser.parse_args()
    result = build(args.research_dir)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {len(result['cases'])} original table eligibility cases: {args.output}")
    for case in result["cases"]:
        print(
            case["name"], case["outcome"], "earlier_busy_guard=" + str(case["earlier_busy_guard"])
        )


if __name__ == "__main__":
    main()
