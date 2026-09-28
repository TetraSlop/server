"""Trade admission consumes pending consent only after the service accepts it."""

import unittest
from dataclasses import dataclass

from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named
from tmserver.protocol.identities import player_id
from tmserver.network.local_accounts import account_specs
from tmserver.economy.trade_router import TradeRouter
from tmserver.economy.trade import TradeError, TradeService


@dataclass(frozen=True)
class Peer:
    nick: bytes


def metadata(peer):
    return f"/NN={player_id(peer.nick):016X}/HID=0/Dm=0/Vol=0"


def entry_request(sender, recipient):
    return named(
        0xA0,
        0,
        0,
        f"@TrEnt=/NN={player_id(sender.nick):016X}|{player_id(recipient.nick):016X}"
        "/HID=0|0/Dm=0|0/Vol=0|0",
    )


class TradeRouterTests(unittest.TestCase):
    def setUp(self):
        self.economy = EconomyService(seed=1)
        self.peers = tuple(Peer(key.encode()) for key in account_specs(5))
        self.trade = TradeService(self.economy)
        self.router = TradeRouter(self.trade, lambda peer: self.trade.status(peer) is not None)
        for peer in self.peers:
            key = peer.nick.decode()
            self.economy.provision(key, nn=player_id(peer.nick))
            self.trade.bind(peer, key)
        a, b, c, d, e = self.peers
        for sender, recipient in ((a, b), (c, a), (b, c), (d, e)):
            self.router.observe(sender, recipient, named(0x21, 0, 0, "@Tr=" + metadata(sender)))
            self.router.observe(
                recipient, sender, named(0x22, 0, 0, "@TrAns=/Ans=1" + metadata(recipient))
            )
        # An unanswered invitation involving an admitted peer expires as well.
        self.router.observe(a, d, named(0x21, 0, 0, "@Tr=" + metadata(a)))

    def test_admission_consumes_all_participant_consent_but_preserves_unrelated_pairs(self):
        a, b, c, d, e = self.peers
        before = self.economy.snapshot()
        self.assertEqual(len(self.router.entry(a, entry_request(a, b))), 4)
        self.assertEqual(self.router.invites, {(d, e)})
        self.assertEqual(self.router.grants, {(d, e)})
        self.assertIsNotNone(self.trade.status(a))
        self.assertIsNotNone(self.trade.status(b))
        self.assertIsNone(self.trade.status(c))
        self.assertEqual(self.economy.snapshot(), before)

    def test_rejected_admission_preserves_pending_pairs_and_existing_reservation(self):
        a, b, *_ = self.peers
        pending = self.router.invites.copy(), self.router.grants.copy()
        key = a.nick.decode()
        reservation = self.economy.reserve_match(key)
        with self.assertRaises(TradeError):
            self.router.entry(a, entry_request(a, b))
        self.assertEqual((self.router.invites, self.router.grants), pending)
        self.assertIsNone(self.trade.status(a))
        self.assertEqual(self.economy.match_account(reservation).key, key)
        self.economy.release_match(reservation)
        self.assertEqual(len(self.router.entry(a, entry_request(a, b))), 4)

    def test_unhandled_entry_does_not_consume_pending_pairs(self):
        a, b, *_ = self.peers
        pending = self.router.invites.copy(), self.router.grants.copy()
        # TrID is peer-directed. This mixed service request is not an admission.
        payload = entry_request(a, b) + b"@TrID=/ID=0000000000000000"
        self.assertIsNone(self.router.entry(a, payload))
        self.assertEqual((self.router.invites, self.router.grants), pending)
        self.assertIsNone(self.trade.status(a))
        self.assertFalse(self.economy.is_reserved(a.nick.decode()))


if __name__ == "__main__":
    unittest.main()
