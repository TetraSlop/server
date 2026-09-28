"""Replay production bounded names with colliding prefixes through original code.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
Synthetic accounts only; no native application, network or saved-file writes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
PREFIX = b"ABCDEFGHIJKLMNO"


def probe(research_dir):
    digest = hashlib.sha256((research_dir / "TM_emulated_mapped.dll").read_bytes()).hexdigest()
    assert digest == CODE_SHA256, digest
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from event_contract_vectors import EM, VIEW, EventVM
    from incoming_gameplay_vectors import ReceiveVM
    from table_admission_vectors import PLAYER, AdmissionVM
    from tmserver.economy.service import EconomyService
    from tmserver.events.models import EventConfig, EventListing
    from tmserver.events.service import EventHost
    from tmserver.events.packets import event_data
    from tmserver.game.match import MatchService
    from unicorn.x86_const import UC_X86_REG_EDX

    rows = []
    for count in (2, 3):
        for restored in (False, True):
            economy = EconomyService(seed=123)
            names = [PREFIX + str(i + 1).encode() for i in range(count)]
            for seat, name in enumerate(names):
                economy.provision(str(seat), nn=PLAYER + seat, player_id=101 + seat, name=name)
            if restored:
                economy = EconomyService.restore(economy.snapshot(), seed=123)
            profiles = [economy.profile_bytes(str(i)) for i in range(count)]
            match = MatchService(mode="versus", player_count=count)
            for seat in range(count):
                account = economy.account_view(str(seat))
                assert account.name == names[seat] and len(account.name) == 16
                match.register(
                    seat, character_id=account.player_id, nickname_id=account.nn, name=account.name
                )

            # Actual production PLAYACK, without replacing its name bytes.
            ack = match.playack(0)
            machine = AdmissionVM()
            machine.queue(ack[5:].decode())
            assert machine.call(0x100869E0, this=machine.controller) == 1
            assert machine.r32(0x1027AA90) == count
            for seat in range(count):
                record = 0x1027A9E8 + seat * 0x38
                assert machine.r64(record) == PLAYER + seat
                assert bytes(machine.u.mem_read(record + 8, 16)) == PREFIX + b"\0"
                assert bytes(machine.u.mem_read(record + 0x24, 16)) == PREFIX + b"\0"
            rows.append(
                dict(
                    kind="PLAYACK",
                    players=count,
                    restored=restored,
                    payload=ack.decode(),
                    ids=[f"{PLAYER + i:016X}" for i in range(count)],
                )
            )

            # Force only the documented post-settings notice phase; the notice
            # builder and complete original controller consumer remain real.
            match.owner_peer = 0
            match.settings_applied = True
            owner = next(
                x.payload for x in match.admission_notice(0) if x.payload.startswith(b"GTM0G1A")
            )
            for seat in range(count):
                parser = AdmissionVM()
                parser.u.mem_write(0x20370000, b"\xa5" * 64)
                parser.queue(owner[5:].decode())
                low = parser.call(0x10084CF0, [0x20370000, 0x20370020], this=parser.controller)
                identity = (low & 0xFFFFFFFF) | parser.u.reg_read(UC_X86_REG_EDX) << 32
                assert identity == PLAYER
                assert bytes(parser.u.mem_read(0x20370000, 16)) == PREFIX + b"\0"
                assert bytes(parser.u.mem_read(0x20370010, 16)) == b"\xa5" * 16
                machine = AdmissionVM()
                machine.w64(0x102B5308, PLAYER + seat)
                machine.queue(owner[5:].decode())
                machine.call(0x100A9180, this=machine.controller)
                assert machine.r8(0x102B6288) == int(seat == 0)
                rows.append(
                    dict(
                        kind="GameOwner",
                        players=count,
                        restored=restored,
                        local_nn=f"{PLAYER + seat:016X}",
                        owner_notice=seat == 0,
                        payload=owner.decode(),
                    )
                )

            init = match.begin()[0].payload
            for seat in range(count):
                machine = ReceiveVM(count, seat)
                machine.w32(0x10294BD4, 101 + seat)
                machine.queue(init[5:].decode())
                assert machine.receive(1) == 1
                mapping = [
                    machine.r8(machine.player + 0x19B + local * 0x30) for local in range(count)
                ]
                assert mapping == [(local + seat) % count for local in range(count)]
                for index in range(count):
                    assert bytes(machine.u.mem_read(0x102B6218 + index * 16, 16)) == PREFIX + b"\0"
                rows.append(
                    dict(
                        kind="VsGameInit",
                        players=count,
                        restored=restored,
                        local_character_id=101 + seat,
                        local_to_server=mapping,
                        payload=init.decode(),
                    )
                )

            config = EventConfig(
                "fixture",
                EventListing(b"Local Lobby", b"Fixture", b"Synthetic"),
                event_data(classes=(b"First", b"Next"), steps_per_class=4),
                100,
                500,
                1001,
                1002,
                1003,
            )
            host = EventHost(economy, (config,), clock=lambda: 200)
            for seat in range(count):
                assert host.admit_room(str(seat), config.id)[0].endswith(b"@EventEn=/Ans=1")
                host.update_progress(
                    str(seat), config.id, f"step-{seat}", steps_delta=11 * (seat + 1)
                )
            if restored:
                host = EventHost.restore(economy, (config,), host.snapshot(), clock=lambda: 200)
            members = host.file("b/g/TM0EventMemberList", config.id)
            for seat in range(count):
                machine = EventVM()
                machine.u.mem_write(EM, members)
                buffer = 0x20190000
                for address, value in (
                    (0x1024F120, buffer),
                    (0x1024F124, buffer),
                    (0x1024F128, buffer + 0x700),
                ):
                    machine.w(address, value)
                # Original bounded-name copy, qword identity copy and vector
                # insertion. Stop before unrelated participant-view layout.
                machine.block(0x100782B8, 0x1007832B, esi=VIEW, ebx=0)
                assert machine.d(0x1024F124) - buffer == count * 0x70
                for index in range(count):
                    record = buffer + index * 0x70
                    assert machine.q(record) == PLAYER + index
                    assert bytes(machine.u.mem_read(record + 0x10, 16)) == PREFIX + b"\0"
                machine.w(0x1024EEB4, buffer)
                machine.w(0x1024EEB8, buffer + count * 0x70)
                machine.w(0x1024EEBC, buffer + 0x700)
                machine.w(0x102B5308, PLAYER + seat)
                machine.w(0x102B530C, PLAYER >> 32)
                machine.call(0x1006C5C0)
                assert machine.d(0x102B5FA8) == 11 * (seat + 1)
                assert machine.d(0x102B5FAC) == count - seat
                rows.append(
                    dict(
                        kind="EventMemberList",
                        players=count,
                        restored=restored,
                        local_nn=f"{PLAYER + seat:016X}",
                        own_steps=machine.d(0x102B5FA8),
                        own_rank=machine.d(0x102B5FAC),
                        file_sha256=hashlib.sha256(members).hexdigest(),
                    )
                )

            assert [economy.account_view(str(i)).name for i in range(count)] == names
            assert [economy.profile_bytes(str(i)) for i in range(count)] == profiles

    return dict(
        code_image_sha256=digest,
        canonical_names=[(PREFIX + str(i + 1).encode()).decode() for i in range(3)],
        displayed_name=PREFIX.decode(),
        case_count=len(rows),
        scope=[
            "Production registration/admission with16-byte account names; exact generated wire/files consumed.",
            "Original PLAYACK, VsGameInit, GameOwner controller and event participant projection/sorter.",
            "Two/three players and fresh/restored synthetic accounts; canonical profile bytes unchanged.",
            "All transmitted names15 bytes plus terminator; no claim of safe16-byte terminated-wire names.",
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
