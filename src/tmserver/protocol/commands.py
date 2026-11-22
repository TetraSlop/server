"""Tetra Master command envelopes, recovered from TM 1.6.11.0.

TM 0x101a2d50 validates; 0x101a3640 constructs; 0x10246298 is the
command dictionary. Payloads stay opaque until each handler is understood.
"""

from dataclasses import dataclass

from tmserver.protocol.messages import service_payload


@dataclass(frozen=True)
class Command:
    token: bytes
    payload: bytes


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


def encode_service(family: bytes, commands: list[Command]) -> bytes:
    """Encode a BEL-family reply with its prefix inside the transport budget."""
    if family not in (b"A", b"L", b"P", b"R"):
        raise ValueError("unsupported BEL service family")
    return service_payload(b"GTM0" + family + encode(commands))
