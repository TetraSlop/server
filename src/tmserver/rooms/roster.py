"""Push ordinary-table membership only after the native admission cleanup."""

from tmserver.game.models import Delivery
from tmserver.game.packets import decode, packet


class TableRoster:
    def __init__(self):
        self.ready = set()
        self.sent = {}
        self.invitations = {}

    def admission(self, peer):
        # GameEA clears queued41/04 even on an explicit admission retry.
        self.ready.discard(peer)
        self.sent.pop(peer, None)

    def publish(self, match, responses):
        out = []
        humans = set(match._humans()) if match.phase == "lobby" else set()
        self.ready.intersection_update(humans)
        self.sent = {peer: body for peer, body in self.sent.items() if peer in self.ready}
        waiting = {p.peer for p in match.players if p.peer in humans and not p.requested}
        self.invitations = {peer: d for peer, d in self.invitations.items() if peer in waiting}
        for delivery in responses:
            msg = decode(delivery.payload)
            if (
                delivery.peer in waiting
                and delivery.peer not in self.ready
                and msg is not None
                and (msg.family, msg.subtype) == (0x41, 7)
            ):
                # A third player may still be completing GameEA when the owner
                # starts. That cleanup discards41/07 as well as the roster.
                self.invitations[delivery.peer] = delivery
            else:
                out.append(delivery)
        if not self.ready:
            return out
        body = match._member_body(match.players)
        roster = packet(0x41, 4, body=body)
        fields = decode(roster).sections["GameML"]
        for delivery in out:
            msg = decode(delivery.payload)
            # Settings, kick and departure already carry the current roster.
            # A manual GameM response has /ID and belongs to another consumer.
            if (
                delivery.peer in self.ready
                and msg is not None
                and msg.sections.get("GameML") == fields
            ):
                self.sent[delivery.peer] = body
        for player in match.players:
            peer = player.peer
            if peer in self.ready and self.sent.get(peer) != body:
                out.append(Delivery(peer, roster))
                self.sent[peer] = body
            if peer in self.ready and peer in self.invitations:
                out.append(self.invitations.pop(peer))
        return out
