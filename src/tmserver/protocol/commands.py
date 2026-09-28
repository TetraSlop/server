"""Tetra Master command envelopes, recovered from TM 1.6.11.0.

TM 0x101a2d50 validates; 0x101a3640 constructs; 0x10246298 is the
command dictionary. Payloads stay opaque until each handler is understood.
"""

import csv
import re
from dataclasses import dataclass

from tmserver.data.resources import read_text

COMMANDS = [
    (int(row["numeric_field"], 16), row["token"].encode("ascii"))
    for row in csv.DictReader(read_text("lobby_commands.tsv").splitlines(), delimiter="\t")
]


@dataclass(frozen=True)
class Command:
    token: bytes
    payload: bytes

    @property
    def ids(self):
        # PO occurs twice. Native lookup returns the first, 0x60.
        return tuple(number for number, token in COMMANDS if token == self.token)


def parse(data: bytes) -> list[Command]:
    """Strict server subset of the client's permissive envelope validator.

    A BEL surrounds the *whole payload*, not individual fields. Multiple
    envelopes can be concatenated. Never guess a payload's field separator.
    """
    if len(data) > 469 or any(c in data for c in (b"\0", b"\r", b"\n")):
        raise ValueError("invalid payload length or line control byte")
    output, position = [], 0
    while position < len(data):
        if data[position : position + 1] != b"<" or data[position + 3 : position + 5] != b">\x07":
            raise ValueError("expected <XX> followed by BEL")
        end = data.find(b"\x07", position + 5)
        if end < 0:
            raise ValueError("missing closing BEL")
        token = data[position + 1 : position + 3]
        if len(token) != 2 or not all(32 <= c <= 126 for c in token):
            raise ValueError("invalid command token")
        output.append(Command(token, data[position + 5 : end]))
        position = end + 1
    return output


def encode(commands: list[Command]) -> bytes:
    data = b"".join(b"<" + c.token + b">\x07" + c.payload + b"\x07" for c in commands)
    if parse(data) != commands:
        raise ValueError("payload contains envelope delimiters")
    return data


def inspect_service(data: bytes):
    """Observed TM service header. Keep the six-byte remainder uninterpreted.

    Native startup captured GTM0GE1000000@TeachDV=. E1 is constructed at
    0x10081203; the receive path waits for E2 at 0x10081311. Do not infer
    the meaning of the six ASCII digits from this one request.
    """
    match = re.fullmatch(rb"GTM0G([0-9A-Fa-f]{2})(.{6})([^\x00\r\n]*)", data)
    if match is None:
        return None
    return {
        "opcode": int(match[1], 16),
        "header_tail_hex": match[2].hex(),
        "body": match[3].decode("latin1"),
    }
