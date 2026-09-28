"""Durable F-service grants and original collection-code comparisons."""

import copy
import json
import struct
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tmserver.economy.models import MAX_MONEY
from tmserver.economy.service import EconomyService
from tmserver.cards.model import Card
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named, parse_named
from tmserver.events.cards import EventCardError, EventCardService
from tmserver.events.models import EventConfig, EventListing
from tmserver.events.service import EventHost
from tmserver.events.packets import event_data


class EventCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.e = EconomyService(seed=8, catalog=self.catalog)
        for key, nn in (("a", 1), ("b", 2)):
            self.e.provision(key, nn=nn, player_id=nn, name=key.encode(), balance=1000)
        self.config = EventConfig(
            "event",
            EventListing(b"Region", b"Event", b"Local"),
            event_data(),
            100,
            500,
            0x1000,
            0x2000,
            0x3000,
            card_service_enabled=True,
        )
        self.now = 200
        self.h = EventHost(self.e, (self.config,), clock=lambda: self.now)
        for key in ("a", "b"):
            self.h.admit_room(key, "event")
        self.s = EventCardService(self.e, self.h)
        self.card = self.e.inventory("a")[0]
        self.number = {}

    def entry(self, key="a", **changes):
        v = self.e.account_view(key)
        fields = dict(NN=f"{v.nn:016X}", HID="0", Dm="1", Vol="1", CN=v.name.hex())
        fields.update(changes)
        request = named(
            0x90, 0, 0, "@CardReq=" + "".join("/" + k + "=" + v for k, v in fields.items())
        )
        replies = self.s.handle(key, request, event_id="event")
        if len(replies) > 1:
            self.number[key] = int(parse_named(replies[1])[3]["EcmInit"]["N"])
        return replies

    def send(self, sub, body, key="a", number=None):
        return self.s.handle(
            key, named(0xB2, sub, self.number[key] if number is None else number, body)
        )

    def get(self, key="a"):
        return self.send(0x15, f"@Get=/No={self.number[key]}", key)

    def quit(self, key="a"):
        return self.send(0x17, f"@Quit=/No={self.number[key]}", key)

    def snapshot(self):
        return dict(economy=self.e.snapshot(), host=self.h.snapshot(), cards=self.s.snapshot())

    def restart(self):
        saved = json.loads(json.dumps(self.snapshot()))
        self.e = EconomyService.restore(saved["economy"], catalog=self.catalog)
        self.h = EventHost.restore(self.e, (self.config,), saved["host"], clock=lambda: self.now)
        self.s = EventCardService.restore(self.e, self.h, saved["cards"])

    @staticmethod
    def edits(token, cards, mode=1):
        return (
            "@"
            + token
            + "="
            + (f"/Mode={mode}" if token == "Erase" else "")
            + f"/C={len(cards)}"
            + "".join(
                f"@{i}=/D="
                + "|".join(map(str, c.reduced() + ((c.deck,) if token == "Decks" else ())))
                for i, c in enumerate(cards)
            )
        )

    def test_grants_are_explicit_owned_and_deduplicated_after_claim(self):
        self.assertTrue(self.s.grant("a", "event", "award1", cards=[self.card], money=25))
        self.assertFalse(self.s.grant("a", "event", "award1", cards=[self.card], money=25))
        before = self.snapshot()
        for kwargs in (
            {"money": 26},
            {"cards": [replace(self.card, deck=1)]},
            {"cards": [self.card] * 31},
        ):
            with self.assertRaises(EconomyError):
                self.s.grant("a", "event", "award1", **kwargs)
        self.assertEqual(self.snapshot(), before)
        self.entry()
        self.get()
        self.quit()
        self.restart()
        self.assertFalse(self.s.grant("a", "event", "award1", cards=[self.card], money=25))
        self.assertEqual(self.s.pending("a", "event"), {"cards": 0, "money": 0})
        self.assertEqual(self.e.balance("a"), 1025)
        self.assertEqual(self.e.balance("b"), 1000)
        self.assertEqual(len(self.e.inventory("b")), 10)

    def test_money_before_init_cards_only_get_and_cached_entry(self):
        self.s.grant("a", "event", "award", cards=[self.card], money=25)
        before = self.e.inventory("a")
        replies = self.entry()
        fields = parse_named(replies[1])[3]["EcmInit"]
        self.assertEqual(
            (parse_named(replies[1])[2], fields["N"], fields["M"], fields["PM"], fields["C"]),
            (0, "1", "1000", "25", "1"),
        )
        self.assertEqual(self.e.balance("a"), 1025)
        self.assertEqual(self.e.inventory("a"), before)
        self.assertEqual(self.entry(), replies)
        with self.assertRaises(EventCardError):
            self.quit()
        self.assertEqual(self.get(), [])
        self.assertEqual(len(self.e.inventory("a")), 11)
        saved = self.snapshot()
        self.assertEqual(self.get(), [])
        self.assertEqual(self.snapshot(), saved)
        fields = parse_named(self.entry()[1])[3]["EcmInit"]
        self.assertEqual((fields["M"], fields["PM"], fields["C"]), ("1025", "0", "0"))
        reply = self.quit()
        self.assertEqual(self.quit(), reply)
        self.assertFalse(self.e.is_busy("a"))

    def test_empty_and_money_only_visits_need_no_get(self):
        for amount in (0, 25):
            if amount:
                self.s.grant("a", "event", "cash", money=amount)
            replies = self.entry()
            self.assertEqual(parse_named(replies[1])[3]["EcmInit"]["C"], "0")
            self.assertTrue(self.s.status("a")["accepted"])
            self.quit()
        self.assertEqual(self.e.balance("a"), 1025)
        self.assertEqual(len(self.e.inventory("a")), 10)

    def test_disconnect_and_restart_preserve_unclaimed_cards_without_repaying_money(self):
        self.s.grant("a", "event", "award", cards=[self.card] * 2, money=25)
        self.entry()
        self.send(0x10, self.edits("Erase", [self.card]))
        self.s.disconnect("a")
        self.h.disconnect("a")
        self.assertEqual(len(self.e.inventory("a")), 10)
        self.restart()
        self.now = 600
        self.assertEqual(parse_named(self.h.admit_room("a", "event")[0])[3]["EventEn"]["Ans"], "1")
        reply = self.entry()
        f = parse_named(reply[1])[3]["EcmInit"]
        self.assertEqual((f["M"], f["PM"], f["C"]), ("1025", "0", "2"))
        self.get()
        self.quit()
        self.restart()
        self.assertEqual(len(self.e.inventory("a")), 12)
        self.assertEqual(self.e.balance("a"), 1025)

    def test_account_auth_and_session_spoof_reject_without_mutation(self):
        self.s.grant("a", "event", "award", cards=[self.card])
        before = self.snapshot()
        for change in ({"NN": "0000000000000002"}, {"CN": b"b".hex()}, {"HID": "4294967296"}):
            with self.assertRaises(EconomyError):
                self.entry(**change)
        self.assertEqual(self.snapshot(), before)
        self.entry()
        before = self.snapshot()
        with self.assertRaises(EventCardError):
            self.send(0x15, "@Get=/No=2", number=2)
        with self.assertRaises(EventCardError):
            self.send(0x15, "@Get=/No=2")
        with self.assertRaises(EventCardError):
            self.send(0x14, "@Buy=/No=6")
        with self.assertRaises(EconomyError):
            self.entry(NN="0000000000000002")
        self.assertEqual(self.snapshot(), before)

    def test_overflow_erase_and_decks_commit_atomically_at_get(self):
        self.e.provision("full", nn=3, player_id=3, name=b"full", cards=[self.card] * 1000)
        self.h.admit_room("full", "event")
        self.s.grant("full", "event", "award", cards=[self.card] * 2)
        self.entry("full")
        before = self.snapshot()
        with self.assertRaises(EconomyError):
            self.get("full")
        self.assertEqual(self.snapshot(), before)
        self.send(0x10, self.edits("Erase", [self.card] * 2), key="full")
        decked = replace(self.card, deck=1)
        self.send(0x22, self.edits("Decks", [decked]) + "@DN1=/S=4576656E74", key="full")
        self.send(0x10, "@Erase=/Mode=2", key="full")
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.get("full")
        self.quit("full")
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.assertEqual(sum(c.deck == 1 for c in self.e.inventory("full")), 1)
        self.assertEqual(dict(self.e.account_view("full").deck_names)[1], b"Event")
        self.assertEqual(self.s.pending("full", "event")["cards"], 0)

    def test_forged_edit_and_multiplicity_are_atomic(self):
        self.s.grant("a", "event", "award", cards=[self.card])
        self.entry()
        before = self.snapshot()
        status = self.s.status("a")
        for body in (
            self.edits("Erase", [replace(self.card, arrows=self.card.arrows ^ 255)]),
            self.edits("Erase", [self.card] * 15),
            "@Erase=/Mode=0/C=0",
        ):
            with self.assertRaises(EconomyError):
                self.send(0x10, body)
        with self.assertRaises(EconomyError):
            self.send(0x22, self.edits("Decks", [replace(self.card, deck=50)]))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.s.status("a"), status)

    def test_more_than_nine_pending_cards_are_claimed_across_bounded_visits(self):
        self.s.grant("a", "event", "bulk", cards=[self.card] * 23)
        counts = []
        for expected in (9, 9, 5):
            reply = self.entry()
            counts.append(int(parse_named(reply[1])[3]["EcmInit"]["C"]))
            self.assertEqual(counts[-1], expected)
            self.assertLessEqual(len(reply[1]) - 13, 447)
            self.get()
            self.quit()
            self.restart()
        self.assertEqual(counts, [9, 9, 5])
        self.assertEqual(len(self.e.inventory("a")), 33)
        self.assertEqual(self.s.pending("a", "event")["cards"], 0)

    def test_wide_card_fields_fit_nine_unfragmented_cards(self):
        high = Card(249, 255, 3, 255, 255, self.catalog.points(249, 255, 255, 255), 255)
        self.e.provision(
            "wide", nn=4, player_id=4, name=b"wide", balance=MAX_MONEY, cards=[high] * 999
        )
        self.h.admit_room("wide", "event")
        self.s.grant("wide", "event", "wideaward", cards=[high] * 9)
        reply = self.entry("wide")
        count = int(parse_named(reply[1])[3]["EcmInit"]["C"])
        self.assertEqual(count, 9)
        self.assertLessEqual(len(reply[1]) - 13, 447)
        self.assertEqual(self.s.pending("wide", "event")["cards"], 9)

    def test_closed_quit_replay_is_bound_to_event_service(self):
        self.entry()
        reply = self.quit()
        request = named(0xB2, 0x17, self.number["a"], f"@Quit=/No={self.number['a']}")
        self.assertEqual(self.s.handle("a", request, event_id="event"), reply)
        with self.assertRaises(EventCardError):
            self.s.handle("a", request, event_id="other")

    def test_grant_queued_during_collection_does_not_invalidate_frozen_batch(self):
        self.s.grant("a", "event", "first", cards=[self.card])
        self.entry()
        self.s.grant("a", "event", "second", cards=[self.card], money=10)
        self.get()
        self.quit()
        self.assertEqual(self.s.pending("a", "event"), {"cards": 1, "money": 10})
        self.entry()
        self.get()
        self.quit()
        self.assertEqual(len(self.e.inventory("a")), 12)
        self.assertEqual(self.e.balance("a"), 1010)

    def test_money_cap_retains_unpaid_remainder(self):
        self.e.provision("rich", nn=3, player_id=3, name=b"rich", balance=MAX_MONEY - 1)
        self.h.admit_room("rich", "event")
        self.s.grant("rich", "event", "cash", money=25)
        reply = self.entry("rich")
        self.assertEqual(parse_named(reply[1])[3]["EcmInit"]["PM"], "1")
        self.quit("rich")
        self.restart()
        self.assertEqual(self.s.pending("rich", "event")["money"], 24)
        token = self.e.reserve_auction("rich")
        self.e.commit_auction(token, debit=100)
        self.entry("rich")
        self.quit("rich")
        self.assertEqual(self.e.balance("rich"), MAX_MONEY - 76)
        self.assertEqual(self.s.pending("rich", "event")["money"], 0)

    def test_exclusivity_and_disabled_advertisement(self):
        token = self.e.reserve_match("a")
        self.assertEqual(parse_named(self.entry()[0])[3]["CardEnt"]["Ans"], "-32870")
        self.assertFalse(self.s.is_active("a"))
        self.e.release_match(token)
        self.entry()
        with self.assertRaises(EconomyError):
            self.e.reserve_match("a")
        with self.assertRaises(EconomyError):
            self.e.reserve_prize("a")
        self.s.disconnect("a")
        self.assertFalse(self.e.is_busy("a"))
        config = replace(self.config, card_service_enabled=False)
        self.h = EventHost.restore(self.e, (config,), self.h.snapshot(), clock=lambda: self.now)
        self.s = EventCardService(self.e, self.h)
        self.assertEqual(parse_named(self.entry()[0])[3]["CardEnt"]["Ans"], "-32870")

    def test_restore_rejects_bad_claim_counts_and_unknown_owner(self):
        self.s.grant("a", "event", "grant", cards=[self.card], money=25)
        snapshot = self.s.snapshot()
        for change in (
            {"card_offset": 2},
            {"money_claimed": 26},
            {"key": "absent"},
            {"event_id": "other"},
            {"cards": [[5, 1, 4, 1, 1, 1, 1, 255]]},
        ):
            bad = copy.deepcopy(snapshot)
            bad["grants"][0].update(change)
            with self.assertRaises(EventCardError):
                EventCardService.restore(self.e, self.h, bad)
        bad = copy.deepcopy(snapshot)
        bad["grants"].append(bad["grants"][0])
        with self.assertRaises(EventCardError):
            EventCardService.restore(self.e, self.h, bad)


if __name__ == "__main__":
    unittest.main()
