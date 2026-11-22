"""Pure shop tests: no sockets, native launches, or filesystem mutations.

Run from server: python -B -m unittest test_economy_service -v
Recorded original-x86 outputs provide independent generator/sale oracles.
"""

import json
import struct
import unittest
from dataclasses import replace
from pathlib import Path

from tests import FIXTURES
from tmserver.economy.models import MAX_MONEY, PROFILE_SIZE
from tmserver.economy.service import EconomyService
from tmserver.cards.model import Card
from tmserver.cards.catalog import Catalog, collection_points
from tmserver.economy.errors import EconomyError
from tmserver.economy.models import ExchangeReservation
from tmserver.economy.packets import named, parse_named
from tmserver.rankings.stats import PracticeStats

PM = FIXTURES / "protocol"


class Draws:
    """Same supplied stream used to record original x86 fixtures, not CRT RNG."""

    def __init__(self, seed, prefix=()):
        self.state, self.prefix, self.used = seed, prefix, 0

    def __call__(self):
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        value = (
            self.prefix[self.used] if self.used < len(self.prefix) else (self.state >> 8) & 0x7FFF
        )
        self.used += 1
        return value


class EconomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.shop = EconomyService(seed=912, catalog=self.catalog)
        self.shop.provision("alice", nn=0xB62DC8D0E4BCBB91, name=b"Alice")
        self.enter()

    def enter(self, key="alice", nn=0xB62DC8D0E4BCBB91, name=b"Alice"):
        replies = self.shop.handle(
            key, named(0xB0, 0, 0, f"@ShReq=/NN={nn:016X}/HID=0/Dm=0/Vol=0/CN={name.hex()}")
        )
        self.assertEqual(parse_named(replies[0])[3], {"ShEnter": {"EN": "1"}})
        _, _, status, sections = parse_named(replies[1])
        self.assertEqual(status, 0)
        self.session = int(sections["Init"]["N"])
        return replies

    def send(self, subtype, body, key="alice", session=None):
        return self.shop.handle(
            key, named(0xB2, subtype, self.session if session is None else session, body)
        )

    @staticmethod
    def records(token, cards, *, decks=False, mode=None):
        head = (
            "@" + token + "=" + (f"/Mode={mode}" if mode is not None else "") + f"/C={len(cards)}"
        )
        return head + "".join(
            f"@{i}=/D=" + "|".join(map(str, c.reduced() + ((c.deck,) if decks else ())))
            for i, c in enumerate(cards)
        )

    def profile_cards(self, key="alice"):
        data = self.shop.profile_bytes(key)
        return [
            Card(*struct.unpack_from("<H7B", data, 0x148 + i * 12))
            for i in range(struct.unpack_from("<H", data, 0x3C)[0])
        ]

    def test_starter_and_profile_identity_options(self):
        data = self.shop.profile_bytes("alice")
        self.assertEqual(len(data), PROFILE_SIZE)
        self.assertEqual(struct.unpack_from("<IQ", data, 4), (1, 0xB62DC8D0E4BCBB91))
        self.assertEqual(data[0x10:0x20].rstrip(b"\0"), b"Alice")
        self.assertEqual(struct.unpack_from("<i", data, 0x34)[0], 10000)
        self.assertEqual(data[0xB3:0xB6], bytes((2, 2, 1)))
        self.assertEqual(data[0x102:0x104], bytes((5, 60)))
        self.assertEqual(self.profile_cards(), list(self.shop.inventory("alice")))
        self.assertEqual(len(self.profile_cards()), 10)
        self.assertEqual(
            struct.unpack_from("<H", data, 0x38)[0], collection_points(self.shop.inventory("alice"))
        )

    def test_init_duplicate_is_stable_and_money_profile_authoritative(self):
        first = self.enter()
        second = self.enter()
        self.assertEqual(first, second)
        self.assertEqual(parse_named(first[1])[3]["Init"]["M"], "10000")

    def test_purchase_commits_on_get_and_quit_uses_same_session(self):
        before = self.shop.inventory("alice")
        reply = self.send(0x14, "@Buy=/No=1")[0]
        family, subtype, seq, parts = parse_named(reply)
        self.assertEqual((family, subtype, seq), (0xB2, 0x14, self.session))
        self.assertEqual(int(parts["Card"]["S"]), 10)
        incoming = [Card(*map(int, parts[f"N{i}"]["D"].split("|"))) for i in range(10)]
        self.assertEqual(self.shop.inventory("alice"), before)
        self.assertEqual(self.shop.balance("alice"), 10000)
        self.assertEqual(self.send(0x15, f"@Get=/No={self.session}"), [])
        self.assertEqual(self.shop.balance("alice"), 9000)
        self.assertEqual(self.shop.inventory("alice"), before + tuple(incoming))
        self.assertEqual(self.profile_cards(), list(before) + incoming)
        quit_reply = self.send(0x17, f"@Quit=/No={self.session}")
        self.assertEqual(
            parse_named(quit_reply[0])[3], {"Quit": {"N": str(self.session), "D": "0", "B": "0"}}
        )
        self.assertEqual(self.send(0x17, f"@Quit=/No={self.session}"), quit_reply)

    def test_pending_retries_replay_without_double_charge(self):
        first = self.send(0x14, "@Buy=/No=2")
        self.assertEqual(self.send(0x14, "@Buy=/No=2"), first)
        self.assertEqual(parse_named(self.send(0x14, "@Buy=/No=1")[0])[3], {"Card": {"S": "0"}})
        self.send(0x15, f"@Get=/No={self.session}")
        committed = self.shop.snapshot()
        self.send(0x15, f"@Get=/No={self.session}")
        self.assertEqual(self.shop.snapshot(), committed)
        self.assertEqual(self.shop.balance("alice"), 8500)
        self.assertEqual(len(self.shop.inventory("alice")), 15)

    def test_refusal_has_no_mutation(self):
        before = self.shop.snapshot()
        for pack in (0, 4, 5, 6, 21, 23, 45, 76, 255):
            with self.subTest(pack=pack):
                self.assertEqual(
                    parse_named(self.send(0x14, f"@Buy=/No={pack}")[0])[3], {"Card": {"S": "0"}}
                )
                self.assertEqual(self.shop.snapshot(), before)

    def test_guild_pack_and_exact_catalogue_price(self):
        reply = self.send(0x14, "@Buy=/No=22")[0]
        self.assertEqual(parse_named(reply)[3]["Card"]["S"], "3")
        self.send(0x15, f"@Get=/No={self.session}")
        self.assertEqual(self.shop.balance("alice"), 7000)

    def test_pauper_recovery_is_explicit_local_policy(self):
        self.shop.provision("poor", nn=2, name=b"Poor", balance=0, cards=[])
        self.enter("poor", 2, b"Poor")
        answer = self.send(0x14, "@Buy=/No=0", key="poor")[0]
        self.assertEqual(parse_named(answer)[3]["Card"]["S"], "5")
        self.send(0x15, f"@Get=/No={self.session}", key="poor")
        self.assertEqual(len(self.shop.inventory("poor")), 5)
        self.assertEqual(self.shop.balance("poor"), 0)

    def test_disconnect_rolls_back_pending_but_not_prior_sales(self):
        c = self.shop.inventory("alice")[0]
        self.send(0x16, self.records("Sell", [c]))
        committed = self.shop.snapshot()
        self.send(0x14, "@Buy=/No=2")
        self.send(0x10, self.records("Erase", [self.shop.inventory("alice")[0]], mode=1))
        self.assertEqual(self.shop.snapshot(), committed)
        self.shop.disconnect("alice")
        self.assertEqual(self.shop.snapshot(), committed)
        self.enter()
        self.assertEqual(len(self.shop.inventory("alice")), 9)

    def test_quit_or_reentry_cannot_skip_pending_get(self):
        self.send(0x14, "@Buy=/No=2")
        with self.assertRaises(EconomyError):
            self.send(0x17, f"@Quit=/No={self.session}")
        with self.assertRaises(EconomyError):
            self.enter()
        self.send(0x15, f"@Get=/No={self.session}")
        self.assertEqual(self.shop.balance("alice"), 8500)

    def test_sell_matches_owned_multiplicity_and_caps_balance(self):
        card = self.shop.inventory("alice")[0]
        self.shop.provision(
            "copies", nn=3, name=b"Copies", cards=[card, card], balance=MAX_MONEY - 1
        )
        self.enter("copies", 3, b"Copies")
        self.send(0x16, self.records("Sell", [card]), key="copies")
        self.assertEqual(self.shop.inventory("copies"), (card,))
        self.assertEqual(
            self.shop.balance("copies"),
            min(MAX_MONEY, MAX_MONEY - 1 + self.catalog.sale_value(card)),
        )
        before = self.shop.snapshot()
        with self.assertRaises(EconomyError):
            self.send(0x16, self.records("Sell", [card, card]), key="copies")
        self.assertEqual(self.shop.snapshot(), before)

    def test_sell_value_and_erase_no_credit(self):
        cards = self.shop.inventory("alice")[:2]
        expected = 10000 + self.catalog.sale_value(cards[0])
        self.send(0x16, self.records("Sell", cards[:1]))
        self.assertEqual(self.shop.balance("alice"), expected)
        self.send(0x10, self.records("Erase", cards[1:], mode=1))
        self.assertEqual(self.shop.balance("alice"), expected)
        self.assertEqual(len(self.shop.inventory("alice")), 8)
        before = self.shop.snapshot()
        self.send(0x10, "@Erase=/Mode=2")
        self.assertEqual(self.shop.snapshot(), before)

    def test_forged_batch_is_atomic(self):
        card = self.shop.inventory("alice")[0]
        forged = replace(card, arrows=card.arrows ^ 255)
        before = self.shop.snapshot()
        for subtype, token in ((0x16, "Sell"), (0x10, "Erase"), (0x22, "Decks")):
            with self.subTest(token=token), self.assertRaises(EconomyError):
                self.send(
                    subtype,
                    self.records(
                        token,
                        [card, forged],
                        decks=token == "Decks",
                        mode=1 if token == "Erase" else None,
                    ),
                )
            self.assertEqual(self.shop.snapshot(), before)

    def test_overflow_can_discard_an_incoming_card_before_get(self):
        card = self.shop.inventory("alice")[0]
        self.shop.provision("full", nn=4, name=b"Full", cards=[card] * 998)
        self.enter("full", 4, b"Full")
        parts = parse_named(self.send(0x14, "@Buy=/No=2", key="full")[0])[3]
        incoming = [Card(*map(int, parts[f"N{i}"]["D"].split("|"))) for i in range(5)]
        with self.assertRaises(EconomyError):
            self.send(0x15, f"@Get=/No={self.session}", key="full")
        self.send(0x10, self.records("Erase", [card, card, incoming[0]], mode=1), key="full")
        self.send(0x15, f"@Get=/No={self.session}", key="full")
        self.assertEqual(len(self.shop.inventory("full")), 1000)
        self.assertEqual(self.shop.balance("full"), 8500)
        self.assertEqual(self.profile_cards("full")[-4:], incoming[1:])

    def test_pending_edits_committed_with_purchase(self):
        old = self.shop.inventory("alice")[0]
        self.send(0x14, "@Buy=/No=2")
        self.send(0x16, self.records("Sell", [old]))
        self.send(0x15, f"@Get=/No={self.session}")
        self.assertEqual(self.shop.balance("alice"), 8500 + self.catalog.sale_value(old))
        self.assertEqual(len(self.shop.inventory("alice")), 14)

    def test_decks_rename_assignment_and_clear(self):
        a, b = self.shop.inventory("alice")[:2]
        self.send(
            0x22,
            self.records("Decks", [replace(a, deck=7)], decks=True) + "@DN1=/S=4661766F7269746573",
        )
        self.assertEqual(self.shop.inventory("alice")[0].deck, 7)
        self.assertEqual(self.shop.profile_bytes("alice")[0x104:0x115].rstrip(b"\0"), b"Favorites")
        self.send(0x22, self.records("Decks", [replace(b, deck=7)], decks=True))
        self.assertEqual([c.deck for c in self.shop.inventory("alice")[:2]], [255, 7])
        self.send(0x22, self.records("Decks", [replace(b, deck=255)], decks=True))
        self.assertEqual(self.shop.inventory("alice")[1].deck, 255)
        self.send(0x22, "@Decks=/C=0@DN2=/S=")
        self.assertEqual(self.shop.profile_bytes("alice")[0x115:0x126], bytes(17))

    def test_decks_can_arrive_after_get(self):
        c = self.shop.inventory("alice")[0]
        self.send(0x14, "@Buy=/No=2")
        self.send(0x15, f"@Get=/No={self.session}")
        self.send(0x22, self.records("Decks", [replace(c, deck=3)], decks=True))
        self.assertEqual(self.shop.inventory("alice")[0].deck, 3)

    def test_invalid_or_truncated_rename_does_not_apply_card_edits(self):
        c = replace(self.shop.inventory("alice")[0], deck=4)
        before = self.shop.snapshot()
        for suffix in ("@DN1=/S=ABC", "@DN3=/S=41", "@DN1=/S=00", "@DN1=/S=" + "41" * 18):
            with self.subTest(suffix=suffix), self.assertRaises(EconomyError):
                self.send(0x22, self.records("Decks", [c], decks=True) + suffix)
            self.assertEqual(self.shop.snapshot(), before)

    def test_invalid_batch_counts_duplicates_slots_and_field_injection(self):
        cards = self.shop.inventory("alice")[:2]
        before = self.shop.snapshot()
        cases = [
            (0x16, "@Sell=/C=16"),
            (0x22, "@Decks=/C=14"),
            (0x10, "@Erase=/Mode=2/C=0"),
            (0x10, "@Erase=/Mode=0/C=0"),
            (0x16, "@Sell=/C=0/M=9999"),
            (0x16, "@Sell=/C=0@0=/D=0|1|0|1|1|0"),
            (0x22, self.records("Decks", [replace(c, deck=7) for c in cards], decks=True)),
            (0x22, self.records("Decks", [replace(cards[0], deck=50)], decks=True)),
        ]
        for subtype, body in cases:
            with self.subTest(body=body), self.assertRaises(EconomyError):
                self.send(subtype, body)
            self.assertEqual(self.shop.snapshot(), before)

    def test_account_session_isolation_and_wrong_body_sequence(self):
        self.shop.provision("bob", nn=9, name=b"Bob")
        before = self.shop.snapshot()
        with self.assertRaises(EconomyError):
            self.send(0x14, "@Buy=/No=2", key="bob")
        with self.assertRaises(EconomyError):
            self.send(0x14, "@Buy=/No=2", session=self.session + 1)
        with self.assertRaises(EconomyError):
            self.send(0x15, f"@Get=/No={self.session+1}")
        with self.assertRaises(EconomyError):
            self.enter("alice", 9, b"Alice")
        self.assertEqual(self.shop.snapshot(), before)

    def test_reconnect_allocates_new_session_and_rejects_stale_edits(self):
        old = self.session
        self.shop.disconnect("alice")
        self.enter()
        self.assertNotEqual(old, self.session)
        with self.assertRaises(EconomyError):
            self.send(0x10, "@Erase=/Mode=2", session=old)

    def test_snapshot_roundtrip_preserves_profile_and_drops_pending(self):
        c = self.shop.inventory("alice")[0]
        self.send(
            0x22, self.records("Decks", [replace(c, deck=12)], decks=True) + "@DN1=/S=52656C6F6164"
        )
        self.send(0x16, self.records("Sell", [self.shop.inventory("alice")[1]]))
        profile = self.shop.profile_bytes("alice")
        self.send(0x14, "@Buy=/No=2")
        restored = EconomyService.restore(
            json.loads(json.dumps(self.shop.snapshot())), seed=2, catalog=self.catalog
        )
        self.assertEqual(restored.profile_bytes("alice"), profile)
        self.assertEqual(restored.inventory("alice"), self.shop.inventory("alice"))
        with self.assertRaises(EconomyError):
            restored.handle("alice", named(0xB2, 0x15, self.session, f"@Get=/No={self.session}"))

    def test_unknown_template_bytes_preserved(self):
        template = bytearray(PROFILE_SIZE)
        template[0x90:0x98] = b"opaque!!"
        template[0xB0:0xB7] = bytes(range(7))
        self.shop.provision("template", nn=4, name=b"Test", profile_template=template, cards=[])
        saved = self.shop.profile_bytes("template")
        self.assertEqual(saved[0x90:0x98], b"opaque!!")
        self.assertEqual(saved[0xB0:0xB7], bytes(range(7)))

    def test_named_parser_limits_and_unrelated_payload(self):
        self.assertIsNone(self.shop.handle("alice", b"GTM0Lanything"))
        self.assertEqual(len(named(0xB2, 0x14, 1, "@X=/A=" + "a" * 441)), 460)
        with self.assertRaises(EconomyError):
            named(0xB2, 0x14, 1, "@X=/A=" + "a" * 442)
        for raw in (
            b"GTM0GB2001401@Buy=/No=1/No=2",
            b"GTM0GB2011401@Buy=/No=1",
            b"GTM0GB2001401@Buy=/No=1\0",
            b"GTM0GB2001401@Buy=/No=1@Buy=/No=2",
        ):
            with self.subTest(raw=raw), self.assertRaises(EconomyError):
                parse_named(raw)

    def test_provision_validates_cards_and_capacity(self):
        card = self.shop.inventory("alice")[0]
        for cards in (
            [card] * 1001,
            [replace(card, model=250)],
            [replace(card, kind=4)],
            [replace(card, points=(card.points + 1) & 255)],
            [replace(card, deck=4)] * 2,
        ):
            with self.subTest(cards=len(cards)), self.assertRaises(EconomyError):
                self.shop.provision("bad", cards=cards)
        with self.assertRaises(EconomyError):
            self.shop.provision("alice")


class ExchangeReservationTests(unittest.TestCase):
    def setUp(self):
        self.shop = EconomyService(seed=7)
        for key, nn in (("a", 1), ("b", 2), ("c", 3)):
            self.shop.provision(key, nn=nn, name=key.encode())
        self.before = self.shop.snapshot()

    def test_reservation_blocks_shop_and_match_and_rollback_is_idempotent(self):
        ticket = self.shop.reserve_exchange(("a", "b"))
        self.assertTrue(self.shop.is_reserved("a"))
        with self.assertRaises(EconomyError):
            self.shop.handle(
                "a", named(0xB0, 0, 0, "@ShReq=/NN=0000000000000001/HID=0/Dm=0/Vol=0/CN=61")
            )
        self.assertEqual(self.shop.snapshot(), self.before)
        self.assertTrue(self.shop.release_exchange(ticket))
        self.assertFalse(self.shop.release_exchange(ticket))
        self.assertFalse(self.shop.is_reserved("a"))

    def test_partial_busy_reservation_does_not_lock_idle_account(self):
        self.shop.reserve_exchange(("a", "b"))
        with self.assertRaises(EconomyError):
            self.shop.reserve_exchange(("c", "b"))
        self.assertFalse(self.shop.is_reserved("c"))
        with self.assertRaises(EconomyError):
            self.shop.reserve_exchange(("c", "c"))

    def test_active_shop_prevents_reservation(self):
        self.shop.handle(
            "a", named(0xB0, 0, 0, "@ShReq=/NN=0000000000000001/HID=0/Dm=0/Vol=0/CN=61")
        )
        with self.assertRaises(EconomyError):
            self.shop.reserve_exchange(("b", "a"))
        self.assertFalse(self.shop.is_reserved("b"))

    def test_atomic_transfer_preserves_money_and_rejects_reused_capability(self):
        ticket = self.shop.reserve_exchange(("a", "b"))
        a, b = self.shop.inventory("a"), self.shop.inventory("b")
        self.shop.commit_exchange(ticket, {"a": a[:-1] + b[-1:], "b": b[:-1] + a[-1:]})
        self.assertEqual(self.shop.inventory("a"), a[:-1] + b[-1:])
        self.assertEqual(self.shop.inventory("b"), b[:-1] + a[-1:])
        self.assertEqual(self.shop.balance("a"), 10000)
        committed = self.shop.snapshot()
        with self.assertRaises(EconomyError):
            self.shop.commit_exchange(ticket, {"a": a, "b": b})
        self.assertEqual(self.shop.snapshot(), committed)

    def test_creation_invalid_names_or_capacity_reject_both_outputs(self):
        ticket = self.shop.reserve_exchange(("a", "b"))
        a, b = self.shop.inventory("a"), self.shop.inventory("b")
        for cards, names in (
            ({"a": a + b, "b": b}, None),
            ({"a": a * 101, "b": b}, None),
            ({"a": a, "b": b}, {"a": {1: b"OK", 2: b"OK"}, "b": {1: b"x" * 18, 2: b"OK"}}),
        ):
            with self.assertRaises(EconomyError):
                self.shop.commit_exchange(ticket, cards, names)
            self.assertEqual(self.shop.snapshot(), self.before)
            self.assertTrue(self.shop.is_reserved("a"))
            self.assertTrue(self.shop.is_reserved("b"))

    def test_forged_reservation_cannot_release_or_commit_original(self):
        real = self.shop.reserve_exchange(("a", "b"))
        forged = ExchangeReservation(real.accounts)
        self.assertFalse(self.shop.release_exchange(forged))
        with self.assertRaises(EconomyError):
            self.shop.commit_exchange(forged, {v.key: v.cards for v in forged.accounts})
        self.assertTrue(self.shop.is_reserved("a"))

    def test_snapshot_restore_drops_reservations_with_no_uncommitted_changes(self):
        self.shop.reserve_exchange(("a", "b"))
        restored = EconomyService.restore(self.shop.snapshot())
        self.assertEqual(restored.snapshot(), self.before)
        self.assertFalse(restored.is_reserved("a"))

    def test_delayed_decks_are_owned_and_blocked_while_reserved(self):
        c = replace(self.shop.inventory("a")[0], deck=3)
        ticket = self.shop.reserve_exchange(("a", "b"))
        with self.assertRaises(EconomyError):
            self.shop.apply_deck_changes("a", [c])
        self.shop.release_exchange(ticket)
        self.shop.apply_deck_changes("a", [c], {1: b"Delayed"})
        self.assertEqual(self.shop.inventory("a")[0].deck, 3)
        self.assertEqual(dict(self.shop.account_view("a").deck_names)[1], b"Delayed")
        before = self.shop.snapshot()
        with self.assertRaises(EconomyError):
            self.shop.apply_deck_changes("a", [replace(c, arrows=c.arrows ^ 255)])
        self.assertEqual(self.shop.snapshot(), before)


class ThreeAccountMatchTests(unittest.TestCase):
    def setUp(self):
        self.e = EconomyService(seed=51)
        self.rounds = []
        self.stats = {}
        for index, key in enumerate(("a", "b", "c")):
            self.e.provision(key, nn=index + 1, player_id=index + 1, name=key.encode())
            token = self.e.reserve_match(key)
            cards = token.account.cards[:5]
            self.rounds.append((token, cards, cards))
            self.stats[key] = PracticeStats.from_profile(self.e.profile_bytes(key)).next_result(
                1 if index == 0 else -1
            )

    def test_three_accounts_commit_together_and_survive_reload(self):
        before = {k: self.e.account_view(k) for k in self.stats}
        refreshed = self.e.commit_match_growth(self.rounds, self.stats)
        self.assertEqual(set(refreshed), {"a", "b", "c"})
        restored = EconomyService.restore(self.e.snapshot())
        for key in self.stats:
            self.assertEqual(
                PracticeStats.from_profile(restored.profile_bytes(key)), self.stats[key]
            )
            self.assertEqual(restored.account_view(key), before[key])
            self.assertTrue(self.e.validate_match_selection(refreshed[key], before[key].cards[:5]))
        after = self.e.snapshot()
        with self.assertRaises(EconomyError):
            self.e.commit_match_growth(self.rounds, self.stats)
        self.assertEqual(self.e.snapshot(), after)

    def test_third_account_forgery_rolls_back_every_participant(self):
        before = self.e.snapshot()
        bad = list(self.rounds)
        token, cards, _ = bad[2]
        bad[2] = (token, cards, (replace(cards[0], arrows=cards[0].arrows ^ 255),) + cards[1:])
        with self.assertRaises(EconomyError):
            self.e.commit_match_growth(bad, self.stats)
        self.assertEqual(self.e.snapshot(), before)
        for token, cards, _ in self.rounds:
            self.assertTrue(self.e.validate_match_selection(token, cards))
        invalid = dict(self.stats)
        invalid["c"] = replace(invalid["c"], count=100)
        with self.assertRaises(EconomyError):
            self.e.commit_match_growth(self.rounds, invalid)
        self.assertEqual(self.e.snapshot(), before)
        with self.assertRaises(EconomyError):
            self.e.commit_match_growth(self.rounds + [self.rounds[0]])
        self.assertEqual(self.e.snapshot(), before)


class OriginalClientOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.vectors = json.loads(
            (PM / "economy_generation_vectors.json").read_text(encoding="utf-8")
        )

    def test_936_selectors_against_original_x86(self):
        checked = 0
        for row in self.vectors["selector_vectors"]:
            if row["filter_mode"] > 3:
                continue
            with self.subTest(pack=row["pack_id"], guild=row["filter_mode"], seed=row["rng_seed"]):
                draws = Draws(row["rng_seed"])
                self.assertEqual(
                    self.catalog.select(row["pack_id"], row["filter_mode"], draws), row["result"]
                )
                self.assertEqual(draws.used, row["rng_calls"])
            checked += 1
        self.assertEqual(checked, 936)

    def test_750_generated_cards_and_sales_against_original_x86(self):
        for row in self.vectors["generator_vectors"]:
            with self.subTest(model=row["model_id"], case=row["case"]):
                draws = Draws(row["rng_seed"], row["rng_prefix"])
                card = self.catalog.generate(row["model_id"], draws)
                self.assertEqual(list(card.full()[1:7]), row["result"])
                self.assertEqual(card.deck, row["deck_slot"])
                self.assertEqual(draws.used, row["rng_calls"])
                self.assertEqual(self.catalog.sale_value(card), row["sale_value"])

    def test_500_catalogue_sale_boundaries_against_original_x86(self):
        vectors = json.loads((PM / "economy_vectors.json").read_text(encoding="utf-8"))[
            "sale_vectors"
        ]
        checked = 0
        for row in vectors:
            model = row["card_id"]
            if not 0 <= model < 250:
                continue
            b = self.catalog.cards[model]
            if row["baseline"] != [
                b["attack"],
                b["type"],
                b["physical_defense"],
                b["magical_defense"],
                b["base_card_points"],
                b["price_tier"],
            ]:
                continue
            if row["price_tier_value"] != self.catalog.prices[b["price_tier"]]:
                continue
            values = row["card"]
            card = Card(model, *values)
            with self.subTest(model=model, stats=values):
                self.assertEqual(self.catalog.sale_value(card), row["stored_value"])
            checked += 1
        self.assertEqual(checked, 500)


if __name__ == "__main__":
    unittest.main()
