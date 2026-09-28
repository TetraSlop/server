"""Original fixed-room admission, editor choice and Apply sender evidence.

Requires the established external protocol_map harness and Unicorn. All accounts
and widgets are synthetic memory; no native application or network is started.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
GAME_KEYS = ("bm", "du", "st", "cb", "ca", "gs", "tl")
SAVED_GAME = dict(bm=0, du=0, st=0, cb=1, ca=0, gs=2, tl=5)


def build(research_dir):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from auction_rank_vectors import Machine as DirectoryMachine, OUT, SCENE
    from directory_ui_vectors import RL
    from room_contract_vectors import CTX, PTL, Machine as TableMachine
    from table_admission_vectors import AdmissionVM
    from tmserver.game.packets import decode
    from tmserver.rooms.config import load_ordinary_rooms
    from tmserver.network.server import Server
    from unicorn.x86_const import (
        UC_X86_REG_EBP,
        UC_X86_REG_EBX,
        UC_X86_REG_EDI,
        UC_X86_REG_ESI,
        UC_X86_REG_ESP,
    )

    def game_bytes(rules):
        return struct.pack("<I6B2x", *(rules[key] for key in GAME_KEYS))

    def game_at(machine, address):
        return dict(zip(GAME_KEYS, struct.unpack("<I6B2x", machine.u.mem_read(address, 12))))

    host = Server(
        account_count=3,
        ordinary_rooms=load_ordinary_rooms(ROOT / "examples/auto_rooms.example.json"),
    )
    account_key = next(iter(host.accounts)).decode("ascii")
    directory = host.profile_file(account_key, "b/g/RL000")
    count = struct.unpack_from("<I", directory, 0x40)[0]
    directory_vm = DirectoryMachine()
    directory_vm.w32(0x1026CDD8, count)
    directory_vm.u.mem_write(RL, directory[0x48 : 0x48 + count * 0xC8])
    for address, value in (
        (0x1024EE44, OUT),
        (0x1024EE48, OUT),
        (0x1024EE4C, OUT + 8 * 0x1D8),
        (0x200FF014, SCENE),
    ):
        directory_vm.w32(address, value)
    directory_vm.block(0x1008E08E, 0x1008E46E, esi=OUT, ebp=SCENE)

    cases = []
    for index, (config, adapter) in enumerate(host.ordinary_room_bindings, 1):
        projected_room = bytes(directory_vm.u.mem_read(OUT + index * 0x1D8, 0x1D8))
        snapshot = adapter.room.snapshot()
        tables = TableMachine()
        tables.initialize()
        # Original selected-room copy includes +18C -> current context102B54EC.
        tables.u.mem_write(0x102B5360, projected_room)
        tables.u.mem_write(PTL, snapshot)
        tables.call(0x101A0C90, CTX, PTL)
        projected = tables.refresh()
        record = bytes(tables.u.mem_read(tables.d(0x1024F110), 0x8B0))
        assert projected["derived_count"] == 1

        machine = AdmissionVM()
        table_address, game_editor, table_editor = 0x20360000, 0x20390000, 0x203A0000
        machine.u.mem_write(0x1024EC68, projected_room)
        machine.branch(0x1003074A, [0x10030761])
        machine.u.mem_write(table_address, record)
        service = struct.unpack_from("<Q", record)[0]
        machine.w64(machine.child + 0x178, service)
        machine.w32(machine.child + 0x184, 2)
        machine.w64(0x102B61D0, 0)
        machine.u.mem_write(0x102B5ED4, game_bytes(SAVED_GAME))
        # Personal table preferences: card-level0..99999, comment8, no password.
        saved_table = bytearray(36)
        struct.pack_into("<5I", saved_table, 0, 0, 99999, 0, 300, 100)
        saved_table[20] = 8
        saved_table[23:31] = b"********"
        machine.u.mem_write(0x102B5EE0, bytes(saved_table))
        machine.hooks[0x100A7E90] = lambda sp: machine.ret(sp, 5000)
        machine.queue("41000200@GameEA=/EN=1", sender=(service & 0xFFFFFFFF, service >> 32))
        machine.branch(
            0x1008F95E,
            [0x1008FEBA],
            {
                UC_X86_REG_EBP: machine.child,
                UC_X86_REG_EDI: 0x12C,
            },
        )
        assert machine.r32(machine.child + 0x184) == 3
        after_admission = game_at(machine, 0x102B61E0)
        context = machine.r32(0x102B61D8)
        assert context == int(config.rules is None)
        if config.rules is not None:
            assert after_admission == config.rules.as_dict()

        events = []

        def post(sp):
            events.append(machine.r32(sp + 4))
            machine.ret(sp, 0, 8)

        machine.hooks[0x1019CCE0] = post
        machine.branch(0x10090114, [0x100901C1], {UC_X86_REG_EBP: machine.child})
        expected_event = 0x3000E79 if context else 0x3000E7A
        assert events == [expected_event]
        game_target, table_target = struct.unpack("<2I", machine.u.mem_read(0x1004DC30, 8))
        constructors = []

        def construct(event):
            machine.w32(machine.controller + 0x64, event)
            target = machine.branch(
                0x1004CA54,
                [game_target, table_target],
                {
                    UC_X86_REG_ESI: machine.controller,
                },
            )
            # Only the generic widget base constructor is a boundary; original
            # allocation, dispatch and argument setup execute up to the editor call.
            machine.hooks[0x10014EE0] = lambda sp: machine.ret(sp, 0, 8)
            constructor = 0x1004EE20 if int(target, 16) == game_target else 0x100516F0
            machine.hooks.pop(int(target, 16))
            machine.branch(
                int(target, 16),
                [constructor],
                {
                    UC_X86_REG_ESI: machine.controller,
                },
            )
            stack = machine.u.reg_read(UC_X86_REG_ESP)
            arguments = [machine.r32(stack + 4)]
            if constructor == 0x100516F0:
                arguments.append(machine.r32(stack + 8))
            constructors.append(
                dict(
                    event=f"{event:08X}",
                    target=target,
                    constructor=f"{constructor:08X}",
                    arguments=arguments,
                )
            )
            return arguments

        arguments = construct(events[0])

        edited_game = None
        if context:
            # Original initial game editor is mode0: personal defaults.
            assert arguments == [0]
            machine.w32(0x200FF154, arguments[0])
            machine.branch(
                0x1004F07E,
                [0x1004F0E3],
                {
                    UC_X86_REG_EBP: game_editor,
                    UC_X86_REG_ESI: 0x1B8,
                },
            )
            edited_game = dict(
                zip(GAME_KEYS, (machine.r32(game_editor + 0x18C + 4 * i) for i in range(7)))
            )
            assert edited_game == SAVED_GAME
            # Widget text reflects the native-initialized amount without a user edit.
            machine.u.mem_write(game_editor + 0x154, str(edited_game["bm"]).encode() + b"\0")
            machine.branch(0x100504DF, [0x1005053F], {UC_X86_REG_EBP: game_editor})
            assert game_at(machine, 0x102B61E0) == SAVED_GAME
            events.clear()
            machine.branch(0x10051356, [0x10051374], {UC_X86_REG_EBP: game_editor})
            assert events == [0x3000E7A]
            arguments = construct(events[0])

        # Initial table editor mode0 reads personal table options in either room.
        # Its constructor's +194 assignment and initializer execute unchanged.
        assert arguments == [context, 0]
        machine.w32(0x200FF13C, arguments[1])
        machine.branch(
            0x100518DC,
            [0x10051906],
            {
                UC_X86_REG_EBP: table_editor,
                UC_X86_REG_ESI: 0x190,
            },
        )
        table_fields = dict(
            co=machine.r32(table_editor + 0x1C4),
            pa=machine.r32(table_editor + 0x1C8),
            in_=machine.r32(table_editor + 0x1CC),
            ll=machine.r32(table_editor + 0x1D0),
            lu=machine.r32(table_editor + 0x1D4),
        )
        assert table_fields == dict(co=8, pa=0, in_=0, ll=0, lu=99999)
        # Text-field boundary: unchanged numeric values from original initialization.
        for offset, value in ((0x174, table_fields["lu"]), (0x17C, table_fields["ll"])):
            machine.u.mem_write(table_editor + offset, str(value).encode() + b"\0")
        machine.branch(
            0x100533A0,
            [0x10053496],
            {
                UC_X86_REG_EBP: table_editor,
                UC_X86_REG_EBX: 100,
                UC_X86_REG_ESI: 300,
                UC_X86_REG_EDI: 0,
            },
        )
        assert len(machine.sends) == 1
        # The harness stops before the generic transport adds its game-route prefix.
        original_payload = machine.sends[0]["payload"]
        wire = "GTM0G" + original_payload
        message = decode(wire.encode("ascii"))
        expected_game = config.rules.as_dict() if config.rules else SAVED_GAME
        actual_game = {key: int(value) for key, value in message.sections["Tet"].items()}
        assert message.primary == 0x14 and actual_game == expected_game
        assert game_at(machine, 0x102B5ED4) == SAVED_GAME
        # The real hosted validator accepts the original packet for this preset.
        adapter.match.register("synthetic-owner", character_id=1)
        assert adapter.match.handle("synthetic-owner", wire.encode("ascii")) == []
        cases.append(
            dict(
                room=config.name.decode(),
                fixed=config.rules is not None,
                saved_game=SAVED_GAME,
                after_admission=after_admission,
                admitted_context=context,
                posted_event=f"{expected_event:08X}",
                constructors=constructors,
                game_editor_values=edited_game,
                table_editor_values=table_fields,
                original_payload=original_payload,
                apply=wire,
                saved_game_unchanged=True,
                server_accepts_apply=True,
                ptl_sha256=hashlib.sha256(snapshot).hexdigest(),
            )
        )

    # Independent control: the noninitial editor reads the Rule13 cache, not
    # saved personal Tet. This path is separate from fixed-room initial Apply.
    machine = AdmissionVM()
    current = dict(bm=123, du=1, st=0, cb=0, ca=1, gs=0, tl=4)
    body = "@Tet=" + "".join(f"/{key}={value}" for key, value in current.items())
    body += "@Tab=/in=0/lu=99999/ll=0/au=300/al=100/co=3/pa=0"
    machine.u.mem_write(0x102B5ED4, game_bytes(SAVED_GAME))
    machine.queue("13000000" + body)
    assert machine.call(0x10086E90, this=machine.controller) == 1
    assert machine.call(0x100873A0, [0x102B5F10, 0x102B5F04, 0]) == 1
    assert game_at(machine, 0x102B5F04) == current
    machine.w32(0x20390000 + 0x150, 1)
    machine.call(0x1004ECA0, this=0x20390000)
    editor = dict(zip(GAME_KEYS, (machine.r32(0x20390000 + 0x18C + i * 4) for i in range(7))))
    assert editor == current
    assert game_at(machine, 0x102B5ED4) == SAVED_GAME

    return dict(
        version=1,
        code_image_sha256=hashlib.sha256(
            (research_dir / "TM_emulated_mapped.dll").read_bytes()
        ).hexdigest(),
        directory_sha256=hashlib.sha256(directory).hexdigest(),
        original=dict(
            room_projection="1008E08E..1008E46E",
            selected_room_copy="1003074A..10030761",
            table_projection=["101A0C90", "10086010", "10066D10"],
            admission="1008F95E..1008FEBA",
            editor_event="10090114..100901C1",
            editor_dispatch="1004CA54, jump table1004DC20",
            game_initialization="1004F07E..1004F0E3 -> 1004ECA0",
            game_commit="100504DF..1005053F",
            table_initialization="100518DC..10051906 -> 100515C0",
            table_commit_and_sender="100533A0..10053496 -> 10087410",
        ),
        boundaries=[
            "In-memory Server with example rooms; no live state file, listener or native process.",
            "Complete original RL/PTL projections, queued GameEA consumer and state copies.",
            "Admission-complete event branch runs after the non-rendered scene transition boundary.",
            "UI event posting is captured; widget construction/rendering is not executed.",
            "Generic widget base construction is stubbed; actual editor call arguments and initializers execute unchanged.",
            "Numeric text fields are seeded from initialized values, representing no user edits.",
            "Original settings serialization executes; final send is captured by the established harness.",
        ],
        cases=cases,
        rule13_control=dict(
            reply="GTM0G13000000" + body,
            saved_game=SAVED_GAME,
            editor_mode=1,
            editor_values=editor,
            saved_game_unchanged=True,
            original=["10086E90", "100873A0", "1004ECA0"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tests/fixtures/fixed_room_apply.json"
    )
    args = parser.parse_args()
    result = build(args.research_dir)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {len(result['cases'])} original admission/editor/Apply chains: {args.output}")


if __name__ == "__main__":
    main()
