"""Native profile response framing and object checksums."""

import struct


def checksum(data):
    whole = len(data) & ~3
    value = sum(struct.unpack("<" + "I" * (whole // 4), data[:whole]))
    if whole < len(data):
        value += int.from_bytes(data[whole:], "little") << (8 * (4 - len(data[whole:])))
    return value & 0xFFFFFFFF


def checked(data):
    return bytes(data) + struct.pack("<I", checksum(data))


def answer(size=0, error=0, nonce=0):
    data = bytearray(24)
    data[0:2] = bytes((0x81, error))
    struct.pack_into("<I", data, 4, size)
    struct.pack_into("<I", data, 20, nonce)
    return data
