"""Execute original offline VS. Rating generation and neutral hosted presentation.

Requires the external protocol_map Unicorn harness and reconstructed TM image.
All state is synthetic. Only final drawing and the supplied random stream are
intercepted beyond the existing harness's presentation/runtime boundaries.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

CODE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"


def probe(research_dir):
    digest = hashlib.sha256((research_dir / "TM_emulated_mapped.dll").read_bytes()).hexdigest()
    assert digest == CODE_SHA256, digest
    sys.path.insert(0, str(research_dir / "protocol_map"))
    from rating_history_vectors import RatingVM

    def setup(machine):
        # Keep100CDA80 intact: it has a consequential T1 reset before drawing.
        machine.hooks[0x100D4770] = lambda sp: machine.ret(sp, cleanup=8)
        machine.hooks[0x100D4810] = lambda sp: machine.ret(sp, cleanup=48)

    def components(machine):
        return [
            struct.unpack("<h", machine.u.mem_read(machine.game + offset, 2))[0]
            for offset in (0x1D8, 0x1DA)
        ]

    def finish(machine, first_tick=200):
        states = []
        for tick in range(first_tick, first_tick + 1200, 100):
            machine.w32(machine.player + 0xD4, tick)
            machine.call(0x100C79C0, this=machine.game)
            state = machine.r8(machine.game + 0x16F)
            states.append(state)
            if state == 0x35:
                return states
        raise AssertionError(("presentation did not reach Ready wait", states))

    offline = []
    scores_list = ([7, 3], [5, 5], [10, 0], [7, 5, 3], [6, 6, 3], [5, 5, 5], [0, 0, 15])
    for scores in scores_list:
        for seat in range(len(scores)):
            for supplied in ((0, 0), (999, 1000), (1499, 1500), (32767, 32767)):
                machine = RatingVM(len(scores), seat)
                setup(machine)
                p, g = machine.player, machine.game
                machine.w8(p + 0xEA, 0)
                machine.w8(p + 0xE9, 0)
                machine.w8(g + 0x196, sum(scores))
                machine.w16(p + 0x84, 1)
                machine.w32(p + 0xB0, 10000)
                machine.w32(p + 0xB4, 777)
                machine.w16(p + 0xB8, 888)
                for local in range(len(scores)):
                    server = (local + seat) % len(scores)
                    machine.w8(p + 0x1A0 + local * 0x30, scores[server])
                    machine.w32(p + 0x180 + local * 0x30, 300)
                    machine.w8(p + 0x19B + local * 0x30, server)
                machine.call(0x100CBD80, this=g)
                order = machine.r8(p + 0x1A5)
                result_class = machine.r8(p + 0x1A1)
                expected_order = sorted(range(len(scores)), key=lambda i: (-scores[i], i)).index(
                    seat
                )
                assert order == expected_order
                assert machine.r32(p + 0xB0) == 10000
                draws = []

                def random_value(sp):
                    value = supplied[len(draws)]
                    draws.append(value)
                    machine.ret(sp, value)

                machine.hooks[0x101E2E44] = random_value
                machine.w8(g + 0x16F, 0x2F)
                machine.w32(g + 0x244, 0)
                machine.w32(p + 0xD4, 100)
                machine.call(0x100C79C0, this=g)
                generated = components(machine)
                expected = (
                    [supplied[0] % 1000, supplied[1] % 1000]
                    if order == 0
                    else [-(supplied[0] % 1500), 0]
                )
                assert generated == expected
                assert draws == list(supplied[: 2 if order == 0 else 1])
                assert machine.r32(p + 0xB0) == 10000
                states = finish(machine)
                # The original formatter clears the second component for
                # class>=2, including first-in-order fully tied results.
                effective = [expected[0], expected[1] if result_class < 2 else 0]
                assert machine.r32(p + 0xB0) == 10000 + sum(effective)
                assert components(machine) == [0, 0]
                assert machine.r32(p + 0xB4) == 777
                assert machine.r16(p + 0xB8) == 888
                offline.append(
                    dict(
                        scores=scores,
                        seat=seat,
                        order=order,
                        result_class=result_class,
                        supplied=supplied,
                        draws=draws,
                        generated=generated,
                        effective=effective,
                        final_rt=machine.r32(p + 0xB0),
                        states=states,
                    )
                )

    neutral = []
    for mode in (1, 2):
        for result_class in (1, 2):
            for total in (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
                machine = RatingVM()
                setup(machine)
                p, g = machine.player, machine.game
                machine.w32(0x10294BD0 + 0x128, total)
                machine.block(0x101011FA, 0x1010130C, esi=p)
                assert machine.r32(p + 0xB0) == total
                machine.w8(p + 0xEA, mode)
                machine.w8(g + 0x18A, 2)
                machine.w8(p + 0x1A1, result_class)
                machine.w32(p + 0xB4, 777)
                payload = "43000D00@ResultData=/R=0|0|0|0|0|0|0|0/P=2@No0=/D=0|0|300|0|0|1|0/T=0|0|65535@No1=/D=0|0|300|0|0|1|0/T=0|0|5"
                machine.queue(payload)
                assert machine.receive(13) == 1
                assert machine.r32(p + 0xB0) == total

                def unexpected_random(sp):
                    raise AssertionError("hosted presentation must not draw offline components")

                machine.hooks[0x101E2E44] = unexpected_random
                machine.w8(g + 0x16F, 0x2F)
                machine.w32(g + 0x244, 0)
                states = finish(machine, 100)
                assert states == [0x30, 0x31, 0x32, 0x33, 0x35]
                assert machine.r32(p + 0xB0) == total
                assert machine.r32(p + 0xB4) == 777
                assert machine.r16(p + 0xB8) == 65535
                # Exact scalar export tail; stop before the final pop/return.
                machine.w32(0x10294BD0 + 0x128, 123)
                machine.block(0x10101620, 0x1010162C, esi=p)
                assert machine.r32(0x10294BD0 + 0x128) == total
                neutral.append(
                    dict(
                        mode=mode,
                        result_class=result_class,
                        imported_rt=total,
                        final_rt=machine.r32(p + 0xB0),
                        exported_rt=total,
                        t2=65535,
                        states=states,
                    )
                )

    machine = RatingVM()
    machine.block(0x1010104C, 0x10101056, esi=machine.player)
    assert machine.r32(machine.player + 0xB0) == 1000000
    assert len(offline) == 72 and len(neutral) == 20
    return dict(
        code_image_sha256=digest,
        case_count=len(offline) + len(neutral) + 1,
        constructor_rt=1000000,
        scope=[
            "Original complete result setup, offline component generation and rating presentation state machine.",
            "Actual profile scalar import/export and ResultData receiver for neutral hosted controls.",
            "Final drawing100D4770/100D4810 intercepted; original100CDA80 including its T1 reset executes.",
            "Supplied CRT output values prove arithmetic/draw counts, not historical hosted random scheduling or policy.",
        ],
        offline=offline,
        neutral_hosted=neutral,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.research_dir), indent=2))


if __name__ == "__main__":
    main()
