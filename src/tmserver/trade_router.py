"""Authenticated invitation bookkeeping around the original peer trade flow.

The adapter relays the original messages unchanged; consent is checked again
before the service accepts TrEnt. No fabricated accept, timer or UI event.
"""

import re

from tmserver.economy_service import parse_named
from tmserver.identities import TRADE_RAW, game_id, player_id
from tmserver.trade_service import TradeError, fields, integer


class TradeRouter:
    def __init__(self, service, busy):
        self.service, self.busy = service, busy
        self.invites = set()
        self.grants = set()

    def forget(self, peer, other=None):
        def keep(pair):
            return peer not in pair if other is None else set(pair) != {peer, other}

        self.invites = {pair for pair in self.invites if keep(pair)}
        self.grants = {pair for pair in self.grants if keep(pair)}

    @staticmethod
    def metadata(peer, values, answer=False):
        fields(values, ("Ans", "NN", "HID", "Dm", "Vol") if answer else ("NN", "HID", "Dm", "Vol"))
        if not re.fullmatch(r"[0-9A-Fa-f]{16}", values["NN"]) or int(values["NN"], 16) != player_id(
            peer.nick
        ):
            raise TradeError("trade invitation identity mismatch")
        integer(values["HID"], 0x7FFFFFFF)
        integer(values["Dm"], 255)
        integer(values["Vol"], 65535)

    def observe(self, sender, recipient, payload):
        if not payload.startswith(b"GTM0G") or payload[5:7].upper() not in (
            b"21",
            b"22",
            b"23",
            b"25",
            b"2A",
            b"A0",
        ):
            return False
        family, subtype, argument, sections = parse_named(payload)
        token = {
            0x21: "Tr",
            0x22: "TrAns",
            0x23: "TrCan",
            0x25: "Tr",
            0x2A: "TrCanAck",
            0xA0: "TrID",
        }[family]
        if subtype or argument or set(sections) != {token} or sender is recipient:
            raise TradeError("invalid peer trade control")
        values = sections[token]
        if family == 0x21:
            self.metadata(sender, values)
            if self.busy(sender) or self.busy(recipient):
                raise TradeError("trade participant is busy")
            self.invites.add((sender, recipient))
            if (recipient, sender) in self.invites:
                pair = tuple(sorted((sender, recipient), key=lambda p: player_id(p.nick)))
                self.grants.add(pair)  # Original simultaneous21 role resolution.
        elif family == 0x22:
            if (recipient, sender) not in self.invites:
                raise TradeError("unsolicited trade answer")
            if values.get("Ans") == "1":
                self.metadata(sender, values, answer=True)
                if self.busy(sender) or self.busy(recipient):
                    raise TradeError("trade participant is busy")
                self.grants.add((recipient, sender))
            elif values == {"Ans": "-1"}:
                self.forget(sender, recipient)
            else:
                raise TradeError("unsupported trade answer")
        elif family == 0xA0:
            if (
                values != {"ID": f"{game_id(TRADE_RAW):016X}"}
                or (sender, recipient) not in self.grants
            ):
                raise TradeError("unapproved trade service announcement")
        else:
            if values:
                raise TradeError("unexpected trade cancellation fields")
            self.forget(sender, recipient)
        return True

    def entry(self, peer, payload):
        if payload[5:7].upper() == b"A0":
            _, _, _, sections = parse_named(payload)
            ids = sections.get("TrEnt", {}).get("NN", "").split("|")
            other = next(
                (
                    q
                    for p, q in self.grants
                    if p is peer and len(ids) == 2 and ids[1].upper() == f"{player_id(q.nick):016X}"
                ),
                None,
            )
            if other is None or self.busy(peer) or self.busy(other):
                raise TradeError("trade entry lacks an available consenting pair")
            self.service.authorize_pair(peer, other)
            result = self.service.handle(peer, payload)
            if result is not None:
                # Admission consumes both participants' outstanding invitations,
                # including approvals involving a third peer. Those approvals
                # cannot authorize a later trade after this one completes.
                self.forget(peer)
                self.forget(other)
            return result
        return self.service.handle(peer, payload)
