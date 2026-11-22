"""Auction wire/file policy and original x86 consumer compatibility."""

import json
import struct
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.auctions.packets import (
    BID_PATH,
    EXHIBIT_PATH,
    METADATA_PATH,
    PENDING_PATHS,
    actor_record,
    actor_wire,
    bid_record,
    descriptor,
    listing_record,
    listing_reply,
)
from tmserver.economy.auctions.service import AuctionService
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.service import EconomyService
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named, parse_named
from tmserver.protocol.identities import game_id
from tmserver.protocol.commands import Command, encode, parse


def fixture():
    e = EconomyService(seed=37)
    for i, key in enumerate(("seller", "buyer", "third"), 1):
        e.provision(key, nn=game_id(i), player_id=i, name=key.encode())
    return e, AuctionEconomy(e)


def actor(e, key):
    a = e.account_view(key)
    return b"\x06".join(
        [
            f"0x{a.nn:016X}".encode(),
            str(a.player_id).encode(),
            b"1",
            b"1",
            b"1",
            a.name,
            *([b"0"] * 6),
        ]
    )


def request(e, key, operation, *fields):
    return b"GTM0A" + encode([Command(operation, actor(e, key)), *fields])


def exhibit_request(e, key="seller", opening=100, hours=1, relists=0):
    c = e.inventory(key)[0]
    a = e.account_view(key)
    ii = (
        c.model,
        e.catalog.sale_value(c),
        a.rating,
        0,
        0,
        1,
        0,
        0,
        c.attack,
        c.physical,
        c.kind,
        c.magical,
        c.points,
        c.arrows,
        0,
        0,
    )
    return request(
        e,
        key,
        b"ER",
        Command(b"SP", str(opening).encode()),
        Command(b"BI", b"20"),
        Command(b"ET", str(hours).encode()),
        Command(b"AM", str(relists).encode()),
        Command(b"II", b"\x06".join(str(v).encode() for v in ii)),
    )


def bid_request(e, key, auction_id, amount, revision):
    return request(
        e,
        key,
        b"BR",
        Command(b"AI", str(auction_id).encode()),
        Command(b"BM", str(amount).encode()),
        Command(b"BC", str(revision).encode()),
    )


def collection_request(e, key):
    a = e.account_view(key)
    return named(0xB0, 1, 0, f"@ShReq=/NN={a.nn:016X}/HID=1/Dm=1/Vol=1/CN={a.name.hex()}")


class AuctionServiceTests(unittest.TestCase):
    def setUp(self):
        self.e, self.a = fixture()
        self.now = 1700000000
        self.s = AuctionService(self.a, clock=lambda: self.now)

    def exhibit(self, **kwargs):
        reply = self.s.handle("seller", exhibit_request(self.e, **kwargs))[0]
        self.assertEqual(parse(reply[5:])[0].token, b"ES")
        return self.a.listings()[-1], reply

    def query(self, kind=b"SI", key="seller", fields=()):
        reply = self.s.handle(key, request(self.e, key, kind, *fields))[0]
        commands = parse(reply[5:])
        self.assertEqual(commands[0].token, b"SS" if kind == b"SI" else b"HS")
        path = commands[0].payload.decode()
        data = self.s.file(key, path)
        self.assertEqual(len(data), int(commands[1].payload) * (0xC0 if kind == b"SI" else 0x48))
        return path, data, reply

    def sold(self):
        item, _ = self.exhibit()
        self.s.handle("buyer", bid_request(self.e, "buyer", item.id, 100, 0))
        self.now = item.expires
        self.a.advance(self.now)
        return self.a.listing(item.id)

    def enter(self, key):
        replies = self.s.handle(key, collection_request(self.e, key))
        session = int(parse_named(replies[0])[3]["Init"]["N"])
        return session, replies

    def get(self, key, session):
        return self.s.handle(key, named(0xB2, 0x15, session, f"@Get=/No={session}"))

    def test_exhibit_bid_and_readonly_search_match_files(self):
        item, reply = self.exhibit()
        self.assertEqual(len(self.e.inventory("seller")), 9)
        self.assertEqual(dict((c.token, c.payload) for c in parse(reply[5:]))[b"BC"], b"0")
        path, data, _ = self.query()
        self.assertEqual(data, listing_record(item))
        before = self.e.snapshot()
        self.query(key="buyer")
        self.assertEqual(self.e.snapshot(), before)
        reply = self.s.handle("buyer", bid_request(self.e, "buyer", item.id, 120, 0))[0]
        self.assertEqual(parse(reply[5:])[0].token, b"BS")
        self.assertEqual(self.e.balance("buyer"), 9880)
        self.assertEqual(self.s.file("seller", path), data)
        _, current, _ = self.query()
        self.assertNotEqual(current, data)
        self.assertEqual(struct.unpack_from("<3I", current, 0x90), (120, self.now, 1))
        _, history, _ = self.query(b"BH", fields=(Command(b"AI", b"1"),))
        self.assertEqual(history, bid_record(self.a.history(1)[0]))

    def test_original_search_filters_and_repeated_card_selectors(self):
        item, _ = self.exhibit(opening=1200)
        fields = (
            Command(b"IO", b"seller"),
            Command(b"AI", b"1"),
            Command(b"MP", b"1001\x062000"),
            Command(b"LT", b"1\x062"),
            Command(b"IC", f"0\x06{item.card.model}\x06{item.card.model}".encode()),
            Command(b"IC", f"10\x06{item.card.kind}\x06{item.card.kind}".encode()),
        )
        self.assertEqual(len(self.query(fields=fields)[1]), 0xC0)
        self.assertEqual(self.query(fields=(Command(b"IO", b"buyer"),))[1], b"")
        self.s.handle("buyer", bid_request(self.e, "buyer", 1, 1220, 0))
        self.assertEqual(len(self.query(fields=(Command(b"IB", b"buyer"),))[1]), 0xC0)
        self.assertEqual(len(self.query(b"BH", fields=(Command(b"IB", b"buyer"),))[1]), 0x48)
        for fields in (
            (Command(b"IC", b"11\x060\x061"),),
            (Command(b"MP", b"2\x061"),),
            (Command(b"AI", b"1"), Command(b"AI", b"2")),
            (Command(b"BD", b"1"),),
        ):
            with self.subTest(fields=fields), self.assertRaises(EconomyError):
                self.s.handle("seller", request(self.e, "seller", b"SI", *fields))

    def test_actor_authentication_and_width_failures_are_atomic(self):
        before = self.e.snapshot()
        ledger = self.a.snapshot()
        raw = exhibit_request(self.e)
        malformed = [
            raw.replace(actor(self.e, "seller"), actor(self.e, "buyer")),
            raw.replace(b"\x06seller\x06", b"\x06wrong\x06"),
            raw.replace(b"<ET>\x071\x07", b"<ET>\x07256\x07"),
            raw.replace(b"<SP>", b"<MP>"),
        ]
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(EconomyError):
                self.s.handle("seller", payload)
            self.assertEqual(self.e.snapshot(), before)
            self.assertEqual(self.a.snapshot(), ledger)

    def test_mapped_failures_do_not_debit_or_remove_again(self):
        item, _ = self.exhibit()
        before = self.e.snapshot()
        cases = [(99, 0, b"-8611"), (100, 1, b"-680"), (MAX_MONEY, 0, b"-8610")]
        for amount, revision, error in cases:
            reply = self.s.handle("buyer", bid_request(self.e, "buyer", item.id, amount, revision))[
                0
            ]
            self.assertEqual(parse(reply[5:]), [Command(b"BF", error)])
            self.assertEqual(self.e.snapshot(), before)
        self.assertEqual(
            parse(self.s.handle("buyer", bid_request(self.e, "buyer", 999, 100, 0))[0][5:]),
            [Command(b"BF", b"-8620")],
        )

    def test_metadata_is_twenty_bytes_and_empty_query_still_has_file(self):
        self.assertEqual(self.s.file("seller", METADATA_PATH), bytes(20))
        self.assertEqual(self.query()[1], b"")
        for price in (1000, 1001, 2000, 2001, 5001):
            self.exhibit(opening=price)
        self.assertEqual(
            struct.unpack("<5I", self.s.file("seller", METADATA_PATH)), (5, 1, 2, 1, 1)
        )

    def test_snapshot_capacity_and_account_scoped_cleanup(self):
        self.s.MAX_SNAPSHOTS = 2
        first, old, _ = self.query()
        second, _, _ = self.query(b"BH")
        other, _, _ = self.query(key="buyer")
        failure = self.s.handle("seller", request(self.e, "seller", b"SI"))[0]
        self.assertEqual(parse(failure[5:]), [Command(b"SF", b"-8621")])
        self.assertEqual(self.s.file("seller", first), old)
        self.assertIsNone(self.s.file("buyer", first))
        self.assertTrue(self.s.delete("seller", EXHIBIT_PATH))
        self.assertIsNone(self.s.file("seller", first))
        self.assertIsNotNone(self.s.file("seller", second))
        self.assertIsNotNone(self.s.file("buyer", other))
        self.assertTrue(self.s.delete("seller", second))
        self.assertTrue(self.s.delete("seller", second))
        self.assertFalse(self.s.delete("seller", PENDING_PATHS[0]))

    def test_collection_all_stages_even_empty_and_terminal_quit_is_proactive(self):
        item = self.sold()
        before = self.e.balance("seller")
        for path in PENDING_PATHS:
            self.assertEqual(self.s.file("seller", path), b"\0")
        session, first = self.enter("seller")
        self.assertEqual([parse_named(x)[1] for x in first], [0x13, 0x20, 0x21])
        self.assertEqual(parse_named(first[1])[3], {"Data": {"M": "100"}})
        self.assertEqual(parse_named(first[2])[3], {"Card": {"E": "0", "S": "0", "C": "0"}})
        self.assertEqual(self.e.balance("seller"), before + 100)
        self.assertTrue(self.e.is_reserved("seller"))
        self.assertEqual(
            parse_named(self.get("seller", session)[0])[3], {"Card": {"E": "0", "S": "0", "C": "0"}}
        )
        final = self.get("seller", session)
        self.assertEqual([parse_named(x)[1] for x in final], [0x20, 0x17])
        self.assertEqual(
            parse_named(final[1])[3], {"Quit": {"N": str(session), "D": "0", "B": "0"}}
        )
        self.assertFalse(self.s.collection_active("seller"))
        self.assertFalse(self.e.is_reserved("seller"))
        for path in PENDING_PATHS:
            self.assertIsNone(self.s.file("seller", path))
        session, first = self.enter("buyer")
        self.assertEqual(len(self.e.inventory("buyer")), 10)
        won = self.get("buyer", session)
        self.assertEqual(parse_named(won[0])[3]["N0"]["D"], "|".join(map(str, item.card.reduced())))
        self.assertEqual(len(self.e.inventory("buyer")), 10)
        self.get("buyer", session)
        self.assertEqual(len(self.e.inventory("buyer")), 11)

    def test_disconnect_preserves_unaccepted_items_but_never_recredits_payment(self):
        self.sold()
        session, _ = self.enter("seller")
        balance = self.e.balance("seller")
        self.s.disconnect("seller")
        self.assertFalse(self.e.is_reserved("seller"))
        later, replies = self.enter("seller")
        self.assertNotEqual(session, later)
        self.assertEqual(parse_named(replies[1])[3], {"Data": {"M": "0"}})
        self.assertEqual(self.e.balance("seller"), balance)
        self.s.disconnect("seller")
        session, _ = self.enter("buyer")
        self.get("buyer", session)
        self.s.disconnect("buyer")
        self.assertEqual(len(self.a.pending("buyer")), 1)
        self.assertEqual(len(self.e.inventory("buyer")), 10)
        session, _ = self.enter("buyer")
        self.get("buyer", session)
        self.get("buyer", session)
        self.assertEqual(len(self.e.inventory("buyer")), 11)

    def test_stale_or_invalid_collection_control_cannot_commit(self):
        self.sold()
        session, _ = self.enter("buyer")
        before = self.e.snapshot()
        for payload in (
            named(0xB2, 0x15, session + 1, f"@Get=/No={session+1}"),
            named(0xB2, 0x15, session, "@Get=/No=99"),
            named(0xB2, 0x17, session, f"@Quit=/No={session}"),
            collection_request(self.e, "buyer"),
        ):
            with self.subTest(payload=payload), self.assertRaises(EconomyError):
                self.s.handle("buyer", payload)
            self.assertEqual(self.e.snapshot(), before)
        self.assertTrue(self.s.collection_active("buyer"))
        self.assertIsNone(self.s.handle("seller", named(0xB0, 0, 0, "@ShReq=")))

    def test_collection_overflow_erase_deck_edits_commit_only_on_get(self):
        item = self.sold()
        base = self.e.inventory("buyer")[0]
        self.e.provision("full", nn=game_id(4), player_id=4, name=b"full", cards=[base] * 1000)
        # A trusted ledger sale creates one pending card for the full inventory.
        extra, _ = self.exhibit()
        self.a.bid("full", extra.id, 100, 0, now=self.now)
        self.now = extra.expires
        self.a.advance(self.now)
        session, _ = self.enter("full")
        self.get("full", session)
        with self.assertRaises(EconomyError):
            self.get("full", session)
        self.assertEqual(len(self.e.inventory("full")), 1000)
        values = "|".join(map(str, base.reduced()))
        self.s.handle("full", named(0xB2, 0x10, session, f"@Erase=/Mode=1/C=1@0=/D={values}"))
        self.s.handle(
            "full", named(0xB2, 0x22, session, f"@Decks=/C=1@0=/D={values}|0@DN1=/S=4E6577")
        )
        self.assertEqual(dict(self.e.account_view("full").deck_names)[1], b"Deck One")
        self.get("full", session)
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.assertEqual(dict(self.e.account_view("full").deck_names)[1], b"New")
        self.assertEqual(sum(c.deck == 0 for c in self.e.inventory("full")), 1)

    def test_queries_are_available_while_collection_owns_account(self):
        self.sold()
        session, _ = self.enter("seller")
        before = self.e.snapshot()
        self.query()
        self.query(b"BH")
        self.assertEqual(self.e.snapshot(), before)
        self.assertTrue(self.e.is_reserved("seller"))
        self.get("seller", session)
        self.get("seller", session)

    def test_pages_fit_native_budget_and_restart_conserves_pending(self):
        card = self.e.inventory("seller")[0]
        pages = AuctionService._card_messages(255, [card] * 30)
        self.assertGreater(len(pages), 1)
        start = 0
        for page in pages:
            self.assertLessEqual(len(page), 460)
            head = parse_named(page)[3]["Card"]
            self.assertEqual(int(head["S"]), start)
            start += int(head["C"])
            self.assertEqual(int(head["E"]), 30)
        self.assertEqual(start, 30)
        self.sold()
        old = self.a.snapshot()
        restored = EconomyService.restore(
            json.loads(json.dumps(self.e.snapshot())), catalog=self.e.catalog
        )
        ledger = AuctionEconomy.restore(restored, json.loads(json.dumps(old)))
        service = AuctionService(ledger, clock=lambda: self.now)
        self.assertEqual(service.file("buyer", PENDING_PATHS[0]), b"\0")
        self.assertEqual(
            service.file("seller", METADATA_PATH), self.s.file("seller", METADATA_PATH)
        )


if __name__ == "__main__":
    unittest.main()
