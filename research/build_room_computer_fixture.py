"""Freeze original room-directory projection and table Computer/Join gates.

Requires the external protocol_map harness and original reconstructed TM image.
Executes isolated original x86 instructions in Unicorn; never launches the game.
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
    from auction_rank_vectors import Machine as DirectoryMachine, OUT, SCENE
    from directory_ui_vectors import RL, digits
    from room_contract_vectors import CTX, PTL, Machine as TableMachine, info, table
    from table_admission_vectors import AdmissionVM
    from tmserver.ordinary_rooms import load_ordinary_rooms
    from tmserver.server import Server
    from unicorn.x86_const import (
        UC_X86_REG_EBP,
        UC_X86_REG_ECX,
        UC_X86_REG_EDI,
        UC_X86_REG_ESI,
    )

    def project_room(flag, kind="A", chat="A"):
        machine = DirectoryMachine()
        machine.w32(0x1026CDD8, 1)
        for address, value in (
            (0x1024EE44, OUT),
            (0x1024EE48, OUT),
            (0x1024EE4C, OUT + 8 * 0x1D8),
            (0x200FF014, SCENE),
        ):
            machine.w32(address, value)
        record = bytearray(0xC8)
        struct.pack_into("<Q", record, 0, 0x31425364758697A8)
        record[0x28] = ord(kind)
        record[0x29] = flag
        record[0x2A:0x35] = b"Local Room*"
        record[0x49:0x50] = b"AAAAADA"
        record[0x6A:0x6D] = digits(1, 3)
        record[0x6D:0x70] = digits(16, 3)
        record[0x70] = ord(chat)
        record[0xB8:0xC4] = b"#LOCALROOM01"
        machine.u.mem_write(RL, bytes(record))
        machine.block(0x1008E08E, 0x1008E46E, esi=OUT, ebp=SCENE)
        assert machine.r32(0x1024EE48) - OUT == 0x1D8
        projected = bytes(machine.u.mem_read(OUT, 0x1D8))
        assert machine.r32(OUT + 0x19C) == flag
        assert machine.r16(OUT + 0x1A0) == ord(chat) - ord("A")
        return bytes(record), projected

    def project_table(kind, state=0, count=0, wager=0, snapshot=None):
        machine = TableMachine()
        metadata = bytearray(info(kind=kind.encode(), count=count, owner=123 if count else 0))
        metadata[30:34] = digits(wager, 4)
        # Exercise nonzero packed flags, time, quit and CP bounds identically for A/B.
        metadata[34:39] = b"PDCBA"
        metadata[39:44] = digits(100, 5)
        machine.initialize(table=table(bytes(metadata), state=state))
        if snapshot is not None:
            metadata = snapshot[0x5878:0x58A9]
            machine.u.mem_write(PTL, snapshot)
            machine.call(0x101A0C90, CTX, PTL)
        projection = machine.refresh()
        assert projection["derived_count"] == 1
        pointer = machine.d(0x1024F110)
        record = bytes(machine.u.mem_read(pointer, 0x8B0))
        assert machine.d(pointer + 0x8A8) == ord(kind)
        return bytes(metadata), record, projection["records"][0]

    def gate(room, record, *, cards=25, balance=0):
        machine = AdmissionVM()
        address, widget = 0x20360000, 0x20380000
        machine.u.mem_write(address, record)
        machine.u.mem_write(0x1024EC68, room)
        # Original selected-room copy, including +19C -> 102B54FC.
        machine.branch(0x1003074A, [0x10030761])
        room_flag = machine.r32(0x102B54FC)
        machine.w64(0x102B61D0, 0)
        machine.w64(0x200FF0F8, struct.unpack_from("<Q", record)[0])
        machine.w32(0x102B64A8, balance)
        machine.w32(0x102B63E0, 50)
        for offset in range(0x190, 0x1AC, 4):
            machine.w32(machine.child + offset, widget + offset)
        machine.w32(machine.child + 0x188, 0)
        calls = []
        machine.hooks[0x100B8B30] = lambda sp: machine.ret(sp, cards)

        def disable(sp):
            calls.append((machine.u.reg_read(UC_X86_REG_ECX), machine.r32(sp + 4)))
            machine.ret(sp, 0, 4)

        machine.hooks[0x1018CEA0] = disable
        # Run original table lookup and mode selection, stopping before widgets allocate.
        targets = struct.unpack("<5I", machine.u.mem_read(0x1004725C, 20))
        chosen = int(machine.branch(0x10045ABC, list(targets) + [0x10047236]), 16)
        mode = targets.index(chosen)
        if mode == 0:
            machine.w32(0x200FF018, address)
            machine.branch(
                0x10045D56,
                [0x10045DC9],
                {UC_X86_REG_EBP: widget + 0x194},
            )
        else:
            # Every nonempty-table constructor builds Computer disabled with hint19.
            start, end = {
                1: (0x10046159, 0x1004617D),
                2: (0x1004682B, 0x10046851),
                3: (0x10046BD0, 0x10046BF4),
                4: (0x10046F57, 0x10046F7D),
            }[mode]
            machine.branch(
                start,
                [end],
                {
                    UC_X86_REG_EBP: widget + 0x194,
                    UC_X86_REG_ECX: widget + 0x194,
                },
            )
        constructor_disabled = bool(calls)
        constructor_hint = machine.r8(machine.child + 0x1B1)
        calls.clear()
        # Original per-frame invalid-table guard can disable an initially available action.
        machine.branch(
            0x100475D3,
            [0x1004768A, 0x10047A0A],
            {UC_X86_REG_ESI: address, UC_X86_REG_EBP: machine.child, UC_X86_REG_EDI: 0},
        )
        update_disabled = any(pointer == widget + 0x194 and value for pointer, value in calls)
        final_hint = machine.r8(machine.child + 0x1B1)
        calls.clear()
        # Ordinary human Join path, including native zero-money and wager comparison.
        machine.branch(
            0x100477BC,
            [0x100479A6],
            {UC_X86_REG_ESI: address, UC_X86_REG_EBP: machine.child, UC_X86_REG_EDI: 0},
        )
        return dict(
            room_flag=room_flag,
            mode=mode,
            constructor_disabled=constructor_disabled,
            constructor_hint=constructor_hint,
            update_disabled=update_disabled,
            disabled=constructor_disabled or update_disabled,
            final_hint=final_hint,
            join_disabled=bool(calls[-1][1]),
            join_hint=machine.r8(machine.child + 0x1B0),
        )

    cases = []
    inputs = [
        ("missing_room_flag", 0, "A", "A", 0, 0, 25, 0, 0, True),
        ("computer_room", 65, "A", "A", 0, 0, 25, 0, 0, False),
        ("computer_room_with_money", 65, "A", "A", 0, 0, 25, 1000, 0, False),
        ("computer_does_not_require_room_wager", 65, "A", "A", 0, 0, 25, 0, 500, False),
        ("noncomputer_room", 66, "A", "A", 0, 0, 25, 0, 0, True),
        ("human_only_table", 65, "A", "B", 0, 0, 25, 0, 0, True),
        ("unspecified_rules_computer_room", 65, "B", "A", 0, 0, 25, 0, 0, False),
        ("four_cards", 65, "A", "A", 0, 0, 4, 0, 0, True),
        ("five_cards", 65, "A", "A", 0, 0, 5, 0, 0, False),
        ("inconsistent_empty_table_count", 65, "A", "A", 0, 1, 25, 0, 0, True),
        ("occupied_human_table", 65, "A", "B", 1, 1, 25, 0, 0, True),
        ("playing_table", 65, "A", "A", 2, 2, 25, 0, 0, True),
    ]
    for name, flag, room_kind, table_kind, state, count, cards, balance, wager, disabled in inputs:
        source_room, room = project_room(flag, room_kind)
        source_table, record, projection = project_table(table_kind, state, count, wager)
        result = gate(room, record, cards=cards, balance=balance)
        assert result["disabled"] is disabled, (name, result)
        expected_hint = (
            19
            if state or count
            else 25
            if flag != 65 or table_kind != "A"
            else 23
            if cards < 5
            else 22
        )
        assert result["final_hint"] == expected_hint, (name, result)
        if name == "computer_does_not_require_room_wager":
            assert result["join_disabled"] and result["join_hint"] == 20
        cases.append(
            dict(
                name=name,
                room_record_hex=source_room.hex(),
                table_metadata=source_table.decode(),
                cards=cards,
                balance=balance,
                wager=wager,
                table=projection,
                result=result,
            )
        )

    # Prefix B keeps the same native ordinary-table rules and human admission path.
    parity = []
    room = project_room(65)[1]
    for state, count in ((0, 0), (1, 1)):
        a = project_table("A", state, count)
        b = project_table("B", state, count)
        differences = [i for i, (left, right) in enumerate(zip(a[1], b[1])) if left != right]
        assert differences == [0x870, 0x8A8]  # Raw metadata copy and decoded type.
        human_gate = gate(room, b[1])
        assert not human_gate["join_disabled"]
        parity.append(
            dict(
                state=state,
                count=count,
                a=a[2],
                b=b[2],
                rules_hex=b[1][0x20:0x54].hex(),
                differing_offsets=[hex(i) for i in differences],
                human_join=human_gate,
                equal_except_type=True,
            )
        )

    # Actual generated default/example files; in-memory synthetic accounts only.
    host = Server(
        account_count=3,
        ordinary_rooms=load_ordinary_rooms(ROOT / "examples/auto_rooms.example.json"),
    )
    account_key = next(iter(host.accounts)).decode("ascii")
    directory = host.profile_file(account_key, "b/g/RL000")
    machine = DirectoryMachine()
    room_count = struct.unpack_from("<I", directory, 0x40)[0]
    machine.w32(0x1026CDD8, room_count)
    machine.u.mem_write(RL, directory[0x48 : 0x48 + room_count * 0xC8])
    for address, value in (
        (0x1024EE44, OUT),
        (0x1024EE48, OUT),
        (0x1024EE4C, OUT + 8 * 0x1D8),
        (0x200FF014, SCENE),
    ):
        machine.w32(address, value)
    machine.block(0x1008E08E, 0x1008E46E, esi=OUT, ebp=SCENE)
    assert machine.r32(0x1024EE48) - OUT == room_count * 0x1D8
    production = []
    for number, adapter in enumerate((host, *(t for _, t in host.ordinary_room_bindings))):
        source = directory[0x48 + number * 0xC8 : 0x48 + (number + 1) * 0xC8]
        assert struct.unpack_from("<Q", source)[0] == adapter.room.room_id
        snapshot = adapter.room.snapshot()
        room = bytes(machine.u.mem_read(OUT + number * 0x1D8, 0x1D8))
        assert struct.unpack_from("<Q", room, 0x180)[0] == adapter.room.room_id
        assert struct.unpack_from("<I", room, 0x188)[0] == number
        metadata, record, projection = project_table(chr(snapshot[0x5878]), snapshot=snapshot)
        result = gate(room, record)
        assert result["disabled"] is (number != 0), result
        assert not result["join_disabled"], result
        rules = dict(
            zip(
                ("bm", "du", "st", "cb", "ca", "gs", "tl"),
                (struct.unpack_from("<I", room, 0x1A4)[0], *room[0x1A8:0x1AE]),
            )
        )
        if number >= 1:
            config = host.ordinary_room_bindings[number - 1][0]
            if config.rules is not None:
                assert rules == config.rules.as_dict(), (config.name, rules)
        production.append(
            dict(
                name=source[0x2A:0x49].split(b"*")[0].decode("ascii"),
                room_record_hex=source.hex(),
                table_metadata=metadata.decode(),
                ptl_sha256=hashlib.sha256(snapshot).hexdigest(),
                table=projection,
                result=result,
                fixed_rules=not bool(struct.unpack_from("<I", room, 0x18C)[0]),
                projected_rules=rules if source[0x28] == ord("A") else None,
            )
        )

    return dict(
        schema_version=1,
        code_image_sha256=hashlib.sha256(
            (research_dir / "TM_emulated_mapped.dll").read_bytes()
        ).hexdigest(),
        original=dict(
            room_projection="1008E08E..1008E46E",
            selected_room_copy="1003074A..10030761",
            table_projection=["101A0C90", "10086010", "10066D10"],
            table_mode="10045ABC..10045BBE",
            computer_constructor="10045D56..10045DC9",
            update_guard="100475D3..1004768A",
            human_join="100477BC..100479A6",
        ),
        boundaries=[
            "Original synthetic RL/PTL consumers, selection copy and action branches execute in Unicorn.",
            "Inventory count is supplied at 100B8B30; widget enable calls are captured at 1018CEA0.",
            "No widget construction, scene rendering, native process, network or live account data.",
            "No claim that directory flags alone enforce hosted authorization or historical room policy.",
        ],
        cases=cases,
        table_prefix_parity=parity,
        production_directory_sha256=hashlib.sha256(directory).hexdigest(),
        production_rooms=production,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tests/fixtures/room_computer_gate.json"
    )
    args = parser.parse_args()
    result = build(args.research_dir)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"Passed {len(result['cases'])} original Computer cases, {len(result['table_prefix_parity'])} A/B parity cases and {len(result['production_rooms'])} actual configured rooms: {args.output}"
    )


if __name__ == "__main__":
    main()
