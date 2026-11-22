"""Native profile response framing and object checksums."""

import struct

from tmserver.protocol.wire import checksum


def checked(data):
    return bytes(data) + struct.pack("<I", checksum(data))


def answer(size=0, error=0, nonce=0):
    data = bytearray(24)
    data[0:2] = bytes((0x81, error))
    struct.pack_into("<I", data, 4, size)
    struct.pack_into("<I", data, 20, nonce)
    return data
