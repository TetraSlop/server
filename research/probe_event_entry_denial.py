"""Trace original EventEn denial through the room-clear sender in isolated x86.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
No native application, network, original-file or saved-account writes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
ROOM = 0x31425364758697A8
PARENT = 0x20170000
WRAPPER = 0x20180000


def probe(research_dir):
    digest = hashlib.sha256((research_dir / "TM_emulated_mapped.dll").read_bytes()).hexdigest()
    assert digest == CODE_SHA256, digest
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from event_contract_vectors import PLAYER, VIEW, EventVM
    from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP

    class LifecycleVM(EventVM):
        def __init__(self):
            self.notices = []
            self.wrappers = []
            self.cleanup_children = []
            self.room_packets = []
            self.transport_checks = []
            self.dialogs = []
            super().__init__()
            self.w(0x102B54E0, ROOM)
            self.w(0x102B54E4, ROOM >> 32)

        def return_value(self, value=0, cleanup=0):
            self.ret(value)
            self.u.reg_write(UC_X86_REG_ESP, self.u.reg_read(UC_X86_REG_ESP) + cleanup)

        def hook(self, u, address, size, data):
            sp = u.reg_read(UC_X86_REG_ESP)
            if address == 0x1019CCE0:
                self.notices.append(self.d(sp + 4))
                self.return_value(cleanup=8)
            elif address == 0x10070BF0:
                self.wrappers.append(dict(reason=self.d(sp + 8), complete=self.d(sp + 12)))
                self.return_value(cleanup=12)
            elif address == 0x100993D0:
                self.cleanup_children.append(
                    dict(address=u.reg_read(UC_X86_REG_ECX), mode=self.d(sp + 4))
                )
                # Observe the constructor, then execute its original body.
            elif address == 0x1019E7E0:
                # Original 101A13D0 and text builder 101A3620 execute fully;
                # intercept only the final external transport submission.
                self.room_packets.append(
                    dict(target=f"{self.q(sp + 4):016X}", payload=self.c(self.d(sp + 16)).decode())
                )
                self.return_value(1)
            elif address in (0x101A1180, 0x101A12A0):
                self.transport_checks.append(f"{address:08X}")
                self.return_value(1)
            elif address == 0x10014EE0:
                self.return_value(cleanup=8)
            elif address == 0x101816C0:
                self.return_value(cleanup=4)
            elif address in (0x100A82D0, 0x100A8300):
                self.return_value(cleanup=4)
            elif address == 0x1019D150:
                self.return_value(200)
            elif address == 0x10195260:
                self.return_value(cleanup=16)
            elif address in (0x10195190, 0x101951D0, 0x100A7AE0):
                self.return_value()
            elif address == 0x1019CD40:
                # One queued internal UI event, then the queue is empty.
                value = getattr(self, "queued_notice", False)
                self.queued_notice = False
                self.return_value(int(value))
            elif address == 0x1019CB60:
                self.dialogs.append(dict(text_address=f"{self.d(sp + 8):08X}"))
                self.return_value(cleanup=24)
            else:
                super().hook(u, address, size, data)

    rows = []
    for answer, entry_state, parent_notice, parent_state in (
        (1, 7, None, None),
        (2, 7, None, None),
        (0x7FFFFFFF, 7, None, None),
        (0, 6, None, None),
        (-32870, 210, 0x03001F75, 13),
        (-1, 200, 0x03001F70, 12),
        (-0x80000000, 200, 0x03001F70, 12),
    ):
        machine = LifecycleVM()
        machine.w(VIEW + 0x18C, 6)
        machine.queue(f"D0000000@EventEn=/Ans={answer}")
        machine.block(0x10096970, 0x10096E08, esi=VIEW)
        assert machine.d(VIEW + 0x18C) == entry_state
        assert not machine.room_packets and not machine.notices
        row = dict(answer=answer, entry_state=entry_state)
        if parent_notice is not None:
            machine.block(0x10096970, 0x10096E08, esi=VIEW)
            assert machine.notices == [parent_notice]
            assert machine.d(VIEW + 0x18C) == entry_state + 1
            machine.block(0x1006F783, 0x1006F994, esi=PARENT, eax=parent_notice)
            assert machine.d(PARENT + 0x188) == parent_state
            row.update(parent_notice=f"{parent_notice:08X}", parent_state=parent_state)
        if answer == -32870:
            machine.block(0x1007022E, 0x1007025D, esi=PARENT)
            assert machine.wrappers == [dict(reason=5, complete=1)]
            # Execute the actual reason-wrapper's child-construction block.
            machine.block(0x10070CCB, 0x10070D45, edi=WRAPPER)
            assert len(machine.cleanup_children) == 1
            assert machine.cleanup_children[0]["mode"] == 0
            child = machine.cleanup_children[0]["address"]
            assert machine.d(child + 0x18C) == 0 and machine.d(child + 0x184) == 0
            states = []
            for expected in (1, 2, 3):
                machine.block(0x100994A0, 0x10099B4E, esi=child)
                states.append(machine.d(child + 0x184))
                assert states[-1] == expected
            expected_pc = f"<PC>\x070x{PLAYER:016X}\x07"
            assert machine.room_packets == [dict(target=f"{ROOM:016X}", payload=expected_pc)], (
                machine.room_packets
            )
            assert machine.transport_checks == ["101A1180", "101A12A0"]
            # Cleanup completion is after its progress display reaches 100.
            machine.w(child + 0x188, 0x42C80000)
            machine.block(0x10099CD6, 0x10099D1A, esi=child)
            assert machine.notices[-1] == 0x03001040
            # Reason 5 shows the rejection dialog only after that event.
            machine.w(WRAPPER + 0x64, machine.notices[-1])
            machine.w(0x201F0000 + 0x14, WRAPPER)
            machine.queued_notice = True
            machine.block(0x10071BD1, 0x10071DB8, edi=WRAPPER, ecx=WRAPPER, eax=5)
            assert machine.dialogs == [dict(text_address="000023D0")]
            row.update(
                exit_reason=5,
                cleanup_mode=0,
                cleanup_states=states,
                packet=machine.room_packets[0],
                cleanup_notice=f"{machine.notices[-1]:08X}",
                rejection_dialog_after_cleanup=True,
            )
        rows.append(row)

    machine = LifecycleVM()
    machine.w(VIEW + 0x18C, 1)
    machine.w(VIEW + 0x184, 1)
    machine.block(0x100994A0, 0x10099B4E, esi=VIEW)
    assert machine.d(VIEW + 0x184) == 2 and not machine.room_packets
    rows.append(dict(cleanup_mode=1, next_state=2, room_clear_skipped=True))

    return dict(
        code_image_sha256=digest,
        case_count=len(rows),
        scope=[
            "Original entry receiver, parent event/state branches, cleanup child allocation and PC sender.",
            "Original rejection-dialog branch exercised only after cleanup-complete notification.",
            "UI base initialization/event queue/dialog output and transport exit completion are bounded stubs.",
            "No graphical rendering, live event service, network timing or historical admission policy inferred.",
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
