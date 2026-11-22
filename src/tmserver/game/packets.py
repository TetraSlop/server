"""Match envelopes in the lenient dialect, and the match's decimal-array fields."""

import re
from functools import partial

from tmserver.game.errors import MatchError
from tmserver.protocol.envelope import encode, parse

_ENVELOPE = re.compile(rb"GTM0G[0-9A-Fa-f]{8}[^\x00\r\n]*")


def numbers(text, count=None):
    if not isinstance(text, str) or not re.fullmatch(r"-?[0-9]+(?:\|-?[0-9]+)*", text):
        raise MatchError("expected decimal field")
    parts = text.split("|")
    if count is not None and len(parts) != count:
        raise MatchError("incorrect numeric element count")
    if any(len(p) > 11 for p in parts):
        raise MatchError("oversized integer")
    return [int(p) for p in parts]


packet = partial(encode, printable=False, error=MatchError)


def decode(payload):
    """None for anything that is not a match envelope, so dispatch can fall through."""
    if not _ENVELOPE.fullmatch(payload):
        return None
    return parse(payload, strict=False, error=MatchError)
