"""Check the Win32 bridge ABI without loading an original game image.

Run with 32-bit Windows Python after building native/original_rules. This
checks the production ctypes binding and closed-state guards, not original
routine results or initialized input validation. No image file is accepted.
"""

import argparse
import ctypes
from pathlib import Path
import sys

from tmserver.game.original import native


def require_equal(actual, expected, label):
    if actual != expected:
        raise RuntimeError(f"{label}: expected {expected!r}, got {actual!r}")


def check_layouts():
    # ABI 3 sizes and field offsets from native/original_rules/api.h. The
    # bridge build independently checks the public request/result sizes.
    layouts = (
        (native._BattleRequest, 48, (0, 16, 32, 36, 40, 44)),
        (native._BattleResult, 32, (0, 16)),
        (native._HandCard, 8, (0, 4)),
        (native._BoardCell, 20, (0, 4, 8, 12, 16)),
        (native._MoveRequest, 564, (0, 4, 8, 12, 24, 64)),
        (native._MoveResult, 8, (0, 4)),
        (native._GrowthRequest, 64, (0, 32, 52, 56, 60)),
        (native._GrowthResult, 40, (0, 32, 36)),
        (native._TargetRequest, 40, (0, 4, 8)),
        (native._TargetResult, 4, (0,)),
    )
    for record, size, offsets in layouts:
        require_equal(ctypes.sizeof(record), size, record.__name__ + " size")
        require_equal(ctypes.alignment(record), 4, record.__name__ + " alignment")
        actual = tuple(getattr(record, name).offset for name, _ in record._fields_)
        require_equal(actual, offsets, record.__name__ + " offsets")


def check_bridge(path):
    if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 4:
        raise RuntimeError("Native bridge checks require 32-bit Windows Python")
    check_layouts()
    bridge = native._load_bridge(path)
    require_equal(bridge.tm_rules_abi(), 3, "bridge ABI")
    draws = []

    @native._Random
    def random(_user, _output):
        draws.append(1)
        return 1

    def check_closed(phase):
        require_equal(bridge.tm_rules_close(), 3, phase + " close")
        for name, request_type, result_type in (
            ("battle", native._BattleRequest, native._BattleResult),
            ("move", native._MoveRequest, native._MoveResult),
            ("growth", native._GrowthRequest, native._GrowthResult),
            ("target", native._TargetRequest, native._TargetResult),
        ):
            function = getattr(bridge, "tm_rules_" + name)
            request, result = request_type(), result_type()
            ctypes.memset(ctypes.byref(result), 0xA5, ctypes.sizeof(result))
            before = bytes(result)
            arguments = [ctypes.byref(request)]
            null_arguments = [None]
            if name != "growth":
                arguments += [random, None]
                null_arguments += [native._Random(), None]
            require_equal(function(*arguments, ctypes.byref(result)), 3, phase + " " + name)
            require_equal(bytes(result), before, phase + " " + name + " output")
            require_equal(function(*null_arguments, None), 3, phase + " null " + name)
        require_equal(draws, [], phase + " callback must not execute")

    check_closed("uninitialized")
    # These buffers have the full allocation extents, but are only ever passed
    # with a null pointer or incorrect length. They must not initialize an
    # engine; no synthetic instructions or original image are loaded/executed.
    image = ctypes.create_string_buffer(0x35B000)
    catalog = ctypes.create_string_buffer(3000)
    for label, arguments in (
        ("null buffers", (None, 0, None, 0)),
        ("null image", (None, len(image), catalog, len(catalog))),
        ("short image", (image, len(image) - 1, catalog, len(catalog))),
        ("null catalog", (image, len(image), None, len(catalog))),
        ("short catalog", (image, len(image), catalog, len(catalog) - 1)),
    ):
        require_equal(bridge.tm_rules_open(*arguments), 2, label)
        check_closed("after " + label)
    require_equal(bridge.tm_rules_abi(), 3, "ABI after rejected calls")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path, help="Built Win32 tm_original_rules.dll")
    args = parser.parse_args(argv)
    check_bridge(args.bridge)
    print("Native ABI 3 passed: seven exports, ten record layouts, closed-state and open guards")
    print("No original image loaded; original-routine parity is outside this check")


if __name__ == "__main__":
    main()
