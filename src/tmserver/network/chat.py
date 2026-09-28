"""Recovered named-chat entry, roster pages and raw text contract.

Counters and membership policy are local; packet shapes and page ordering are
from CHAT_SUPPLEMENT.md/CHAT_LIFECYCLE.md. Byte text is never slash-split.
"""

import re
from dataclasses import dataclass

from tmserver.game.packets import decode, packet


@dataclass(frozen=True)
class Member:
    peer: object
    identity: int
    name: bytes


@dataclass(frozen=True)
class Delivery:
    peer: object
    group: bytes
    payload: bytes


def validate_name(name, *, limit=15):
    if (
        not isinstance(name, bytes)
        or not 1 <= len(name) <= limit
        or any(c < 32 or c > 126 for c in name)
    ):
        raise ValueError(f"chat name must contain 1..{limit} printable bytes")


def display_name(canonical_name):
    """Keep native account names intact; chat presentation accepts at most15 bytes."""
    validate_name(canonical_name, limit=16)
    return canonical_name[:15]


def roster_pages(members, counter):
    if type(counter) is not int or not 0 <= counter <= 0x7FFFFFFF:
        raise ValueError("chat counter outside positive signed32 domain")
    members = tuple(members)
    for member in members:
        validate_name(member.name)
        if not 0 < member.identity <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("invalid chat identity")

    def body(index, group):
        return (
            f"@Chat=/C={counter}/Num={index}|{len(group)}/NN="
            + "|".join(f"{m.identity:016X}" for m in group)
            + "/CN="
            + "|".join(m.name.hex().upper() for m in group)
        )

    groups = [[]]
    for member in members:
        if len(body(len(groups) - 1, groups[-1] + [member])) > 447:
            groups.append([])
        groups[-1].append(member)
    if len(groups) > 17:
        raise ValueError("chat roster exceeds original page array")
    return tuple(packet(0x42, 4, len(groups) - 1, body(i, group)) for i, group in enumerate(groups))


def relay_text(payload, name, *, private):
    if not payload.startswith(b"GTM0G42"):
        return None
    name = display_name(name)
    match = re.fullmatch(rb"GTM0G420008([0-9A-Fa-f]{2})([^\x00\r\n]*)", payload)
    if match is None:
        raise ValueError("unsupported or malformed chat message")
    if int(match[1], 16) != int(private):
        raise ValueError("chat display attribute does not match destination")
    body = match[2]
    if len(body) > 447:
        raise ValueError("chat body exceeds client contract")
    fields = re.fullmatch(rb"@Chat=/La=(-?[0-9]{1,11})/Dt=#CHAT#([^\t]*)\t(.*)", body)
    if fields is None:
        raise ValueError("chat locale/name/text fields are required")
    locale = int(fields[1])
    if not -(1 << 31) <= locale < (1 << 31):
        raise ValueError("chat locale outside signed32")
    # Native senders copy the full canonical name. Validate that input bound,
    # then replace it with the authenticated account's shorter display name.
    validate_name(fields[2], limit=16)
    # Account-owned display identity; preserve raw text, including slashes,
    # at signs and non-ASCII locale bytes after the native TAB separator.
    outgoing = b"@Chat=/La=" + str(locale).encode() + b"/Dt=#CHAT#" + name + b"\t" + fields[3]
    if len(outgoing) > 447:
        raise ValueError("normalized chat exceeds client contract")
    return b"GTM0G420008" + (b"01" if private else b"00") + outgoing


class ChatService:
    def __init__(self):
        self.groups = {}
        self.counters = {}

    def enter(self, peer, group, payload, *, identity, name):
        if not payload.startswith(b"GTM0G420001"):
            return None
        message = decode(payload)
        if message is None or message.argument != 0 or set(message.sections) != {"Chat"}:
            raise ValueError("invalid chat entry")
        fields = message.sections["Chat"]
        nn = fields.get("NN", "")
        if not re.fullmatch("[0-9A-Fa-f]{16}", nn) or int(nn, 16) != identity:
            raise ValueError("chat identity is not the authenticated account")
        name = display_name(name)
        members = self.groups.setdefault(group, {})
        candidate = Member(peer, identity, name)
        changed = members.get(peer) != candidate
        members[peer] = candidate
        result = [Delivery(peer, group, packet(0x42, 2, body="@Chat=/EN=1"))]
        result += (
            self._publish(group)
            if changed
            else [
                Delivery(peer, group, page)
                for page in roster_pages(members.values(), self.counters.get(group, 0))
            ]
        )
        return result

    def _publish(self, group):
        counter = self.counters.get(group, 0) + 1
        if counter > 0x7FFFFFFF:
            raise ValueError("chat group counter exhausted; reconnect required")
        pages = roster_pages(self.groups[group].values(), counter)
        self.counters[group] = counter
        return [Delivery(peer, group, page) for peer in self.groups[group] for page in pages]

    def leave(self, peer, group=None):
        result = []
        for key, members in list(self.groups.items()):
            if group is not None and key != group:
                continue
            if peer not in members:
                continue
            del members[peer]
            if members:
                result += self._publish(key)
            else:
                self.groups.pop(key)
        return result
