"""Freeze original hosted Computer defaults, rule editing and final messages.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
All room/player/widget memory is synthetic; no game, network or save is opened.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
GAME_KEYS = ("bm", "du", "st", "cb", "ca", "gs", "tl")


def build(research_dir):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from table_admission_vectors import AdmissionVM
    from tmserver.match_service import MatchError, MatchService, packet
    from tmserver.room_rules import RoomRules
    from unicorn.x86_const import (
        UC_X86_REG_EBP,
        UC_X86_REG_ECX,
        UC_X86_REG_EDI,
        UC_X86_REG_ESI,
    )

    def menu(preset):
        service = MatchService(
            computer_menu=True,
            room_rules=preset,
            allow_wagers=True,
            round_authorizer=lambda match, *_: {
                seat: p.balance for seat, p in enumerate(match.players) if p.peer is not None
            },
            payout_provider=lambda *_: 0,
        )
        service.register("alice", character_id=3, nickname_id=0x123, points=222, collection_size=5)
        service.handle("alice", packet(0x41, 0x12, body="@GameENC=/NN=0000000000000123/HID=3"))
        deliveries = service.handle("alice", packet(1, body="@Req=/NN=0000000000000123/HID=3"))
        init = next(d.payload for d in deliveries if b"@ComGameInit=" in d.payload)
        return service, init

    def outcome(preset, payload):
        service, _ = menu(preset)
        before = service.rng.state
        try:
            service.handle("alice", payload.encode("ascii"))
        except MatchError as error:
            assert service.phase == "computer_menu" and service.rng.state == before
            return dict(accepted=False, error=str(error), phase=service.phase, rng_unchanged=True)
        return dict(accepted=True, phase=service.phase, game_rules=dict(service.game_rules))

    def values(machine):
        p = machine.player
        return dict(
            bm=machine.r32(p + 0xDC),
            st=machine.r8(p + 0xE0),
            cb=machine.r8(p + 0xE1),
            ca=machine.r8(p + 0xE2),
            tl=machine.r8(p + 0xE3),
            du=machine.r8(p + 0xE4),
            view=machine.r8(p + 0xD9),
            gs=machine.r8(p + 0xE9),
        )

    def final_sender(machine):
        machine.w8(machine.player + 0xE5, 2)
        machine.w8(machine.player + 0x1CA, 1)
        machine.w8(machine.player + 0xEA, 1)
        machine.branch(0x100EBBD0, [0x100EBBEE])
        return "GTM0G" + machine.sends[-1]["payload"]

    cases = []
    for name, preset, saved_gs in (
        ("fixed_gs0_saved2", RoomRules(gs=0, st=1, ca=1, tl=4), 2),
        ("fixed_gs1_saved2", RoomRules(gs=1, st=1, ca=1, tl=4), 2),
        ("fixed_gs2_saved0", RoomRules(gs=2, st=1, ca=1, tl=4), 0),
        ("fixed_gs0_saved0", RoomRules(gs=0, st=1, ca=1, tl=4), 0),
        ("custom_saved2", None, 2),
        ("fixed_du1_optional_editor", RoomRules(gs=0, du=1, tl=4), 2),
    ):
        _, init = menu(preset)
        machine = AdmissionVM()
        saved = dict(bm=200, du=1, st=0, cb=1, ca=0, gs=saved_gs, tl=5)
        saved_bytes = struct.pack("<I6B2x", *(saved[key] for key in GAME_KEYS))
        machine.u.mem_write(0x102B5ED4, saved_bytes)
        # Seed both selected-room and current-table fixed/custom context. The
        # original Computer menu does not read the ordinary editor's context.
        context = int(preset is None)
        machine.w32(0x102B54EC, context)
        machine.w32(0x102B61D8, context)
        if preset is not None:
            native_rules = struct.pack("<I6B2x", *(preset.as_dict()[key] for key in GAME_KEYS))
            machine.u.mem_write(0x102B5504, native_rules)
            machine.u.mem_write(0x102B61E0, native_rules)
        machine.w8(machine.player + 0xE9, saved_gs)
        machine.w8(machine.game + 0x172, 1)
        for offset in (0x44, 0x50, 0x54):
            machine.w32(0x200FF000 + offset, 0)
        machine.hooks[0x101881D0] = lambda sp: machine.ret(sp)
        machine.branch(0x100B9B0A, [0x100B9DA7], {UC_X86_REG_ESI: machine.game})
        after_constructor = values(machine)
        assert after_constructor["gs"] == 0

        machine.queue(init[5:].decode("ascii"))
        assert machine.receive(2) == 1
        after_init = values(machine)
        assert after_init["gs"] == 0
        initial_start = final_sender(machine)
        initial_result = outcome(preset, initial_start)
        assert initial_result["accepted"] == (preset is None or preset.gs == 0)

        # Actual settings event -> parent state5 -> rule-editor constructor.
        parent, button = 0x20390000, 0x203A0000
        machine.w32(parent + 0x118, button)
        machine.u.mem_write(parent + 0x2E, struct.pack("<H", 0x1F4))
        machine.hooks[0x1018E450] = lambda sp: machine.ret(sp, cleanup=4)
        machine.branch(
            0x100EB99D,
            [0x100EBC0B],
            {UC_X86_REG_EBP: parent, UC_X86_REG_EDI: 0x0400002F},
        )
        assert machine.r8(parent + 0x110) == 5
        machine.hooks[0x1017C5B0] = lambda sp: machine.ret(sp)
        machine.hooks[0x101997D0] = lambda sp: machine.ret(sp, cleanup=12)
        machine.hooks[0x10195190] = lambda sp: machine.ret(sp)
        machine.branch(0x100EBCAF, [0x100ED480], {UC_X86_REG_EBP: parent})
        editor = machine.u.reg_read(UC_X86_REG_ECX)
        machine.hooks.pop(0x100ED480)
        machine.branch(0x100ED480, [0x100ED58E], {UC_X86_REG_ECX: editor})
        after_editor_open = values(machine)
        indices = list(machine.u.mem_read(editor + 0x153, 5))
        assert after_editor_open["du"] == 0
        assert after_editor_open["gs"] == 0
        # Change the native time-limit selector; leave all other widgets at
        # their original constructor values. This remains possible in fixed rooms.
        machine.w8(editor + 0x156, (after_init["tl"] + 1) % 6)
        machine.branch(0x100EE8E5, [0x100EE960], {UC_X86_REG_EBP: editor})
        edited_save = "GTM0G" + machine.sends[-1]["payload"]
        edited_rules = values(machine)
        edited_start = final_sender(machine)
        assert edited_rules["tl"] != after_init["tl"] and edited_rules["gs"] == 0
        assert bytes(machine.u.mem_read(0x102B5ED4, 12)) == saved_bytes
        edited_save_result = outcome(preset, edited_save)
        edited_start_result = outcome(preset, edited_start)
        assert edited_save_result["accepted"] == (preset is None)
        assert edited_start_result["accepted"] == (preset is None)
        cases.append(
            dict(
                name=name,
                preset=None if preset is None else preset.as_dict(),
                room_context=context,
                saved_tet=saved,
                after_constructor=after_constructor,
                production_init=init.decode("ascii"),
                after_init=after_init,
                initial_start=initial_start,
                initial_result=initial_result,
                settings_event="0400002F",
                parent_state_after_event=5,
                initial_editor_indices=indices,
                after_editor_open=after_editor_open,
                edited_rules=edited_rules,
                edited_save=edited_save,
                edited_save_result=edited_save_result,
                edited_start=edited_start,
                edited_start_result=edited_start_result,
                saved_tet_unchanged=True,
            )
        )
    return dict(
        evidence="Original isolated x86 instructions with production ComGameInit and synthetic room/player/widget state.",
        code_image_sha256=hashlib.sha256(
            (research_dir / "TM_emulated_mapped.dll").read_bytes()
        ).hexdigest(),
        original=dict(
            hosted_constructor="100B9B0A..100B9DA7; BL==0 at100B9D1B, gs write100B9DA1",
            complete_init_receiver="10101A10 subtype2 -> 10102D21..10103039",
            settings_event="100EB99D..100EBC0B event0400002F -> 100EBABB state5",
            settings_open="100EBCAF..100EBD52 -> 100ED480",
            editor_initialization="100ED480..100ED58E; optional du reset100ED4F6",
            editor_apply="100EE8E5..100EE960; original43/06 header0 sender",
            final_sender="100EBBD0..100EBBEE -> 10106E30; gs serialized101071E8",
        ),
        boundaries=[
            "Direct in-memory MatchService permits hypothetical incompatible presets independently of public startup validation.",
            "No listener, native application, original asset write or live save access.",
            "Constructor slice ends before unrelated collection/grade initialization10101530; that helper does not write gs.",
            "Full original queued ComGameInit/ComList parser and complete final serializer execute unchanged.",
            "Resource prefetch, generic widget base constructors, widget selection, and final transport are stubbed.",
            "Actual settings event and editor initialization execute; widget rendering and physical input are not simulated.",
            "NPC choice is seeded to original ID1; the known opponent-selection loop does not change these rules.",
            "Synthetic zero-stake authorization returns unchanged balances; no persistence is involved.",
        ],
        cases=cases,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tests/fixtures/computer_room_rules.json"
    )
    args = parser.parse_args()
    result = build(args.research_dir)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {len(result['cases'])} original Computer rule chains: {args.output}")
    for case in result["cases"]:
        print(case["name"], case["initial_start"], case["initial_result"])


if __name__ == "__main__":
    main()
