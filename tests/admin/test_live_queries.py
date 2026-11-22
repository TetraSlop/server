"""Live operator projections preserve service ownership and claim semantics."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.events.test_event_rooms import config, presence
from tests.rooms.test_ordinary_rooms import EXTRA
from tmserver.admin.queries import (
    account_detail,
    account_rows,
    claim_rows,
    room_rows,
    server_overview,
)
from tmserver.events.models import EventTableConfig
from tmserver.network.server import Server
from tmserver.protocol.identities import game_id
from tmserver.rooms.config import OrdinaryTableConfig
from tmserver.rooms.rules import RoomRules


def make_host():
    fixed = replace(
        EXTRA,
        name=b"Fixed rules",
        rules=RoomRules(st=1, cb=1, gs=0, tl=4),
        tables=(OrdinaryTableConfig(0x202, b"#CPUTABLE", mode="computer"),),
    )
    event = config(match_enabled=True, card_service_enabled=True)
    host = Server(
        account_count=3,
        ordinary_rooms=(fixed,),
        event_configs=(event,),
        event_tables=(
            EventTableConfig("festival", game_id(0x1003), b"#EVTABLE001"),
            EventTableConfig("festival", game_id(0x1004), b"#EVTABLE002"),
        ),
        event_clock=lambda: 200,
    )
    keys = [host.ensure_account(nick) for nick in host.accounts]
    return host, keys


class LiveQueryTests(unittest.TestCase):
    def setUp(self):
        self.host, self.keys = make_host()

    def prohibit_writes(self):
        for method in (
            "ensure_account",
            "advance_auctions",
            "advance_rankings",
            "persist",
            "persist_changes",
        ):
            self.enterContext(patch.object(self.host, method, side_effect=AssertionError(method)))

    def enter_room(self, room, key, peer):
        owner = self.host.economy.account_view(key)
        room.handle(
            peer,
            presence(room.room_id, owner.nn, owner.player_id, owner.name),
            authenticated_identity=(owner.nn, owner.player_id),
        )
        return owner

    def test_saved_unicode_keys_and_live_connection_status_do_not_enroll_accounts(self):
        host = self.host
        key = "joueur-é_日本"
        host.economy.provision(key, nn=987654, player_id=77, name=b"Guest", balance=1234)
        host.account_keys.add(key)
        host.nicks[self.keys[0].encode()] = SimpleNamespace(closed=False)
        host.nicks[self.keys[1].encode()] = SimpleNamespace(closed=True)
        token = host.economy.reserve_match(self.keys[0])
        self.prohibit_writes()
        before = deepcopy(host.snapshot())
        rows = {row["id"]: row for row in account_rows(host)}
        self.assertEqual(set(rows), set(self.keys) | {key})
        self.assertEqual(
            (rows[key]["name"], rows[key]["balance"], rows[key]["connected"]),
            ("Guest", 1234, False),
        )
        self.assertTrue(rows[self.keys[0]]["connected"])
        self.assertTrue(rows[self.keys[0]]["busy"])
        self.assertFalse(rows[self.keys[1]]["connected"])
        self.assertFalse(rows[self.keys[2]]["connected"])
        self.assertEqual(account_detail(host, key)["account_key"], key)
        self.assertEqual(host.snapshot(), before)
        host.economy.release_match(token)

    def test_unknown_key_is_not_replaced_by_a_same_name_account(self):
        self.prohibit_writes()
        name = account_rows(self.host)[0]["name"]
        for key in (name, "unknown", "", "other\0account"):
            with self.subTest(key=key), self.assertRaises(KeyError):
                account_detail(self.host, key)

    def test_pending_points_money_cards_and_active_escrow_are_separate(self):
        host = self.host
        a, b, c = self.keys
        host.prize.grant_award(a, "weekly", top30=10, lucky=3, rookies=2)
        host.events.admit_room(a, "festival")
        cards = tuple(replace(card, deck=255) for card in host.economy.inventory(a)[:2])
        host.event_cards.grant(a, "festival", "event-award", cards=cards, money=75)
        sale = host.auction_ledger.exhibit(a, host.economy.inventory(a)[0], 100, 20, 1, 0, now=100)
        sale = host.auction_ledger.bid(b, sale.id, 100, sale.revision, now=101)
        sale = host.auction_ledger.bid(c, sale.id, 120, sale.revision, now=102)
        host.auction_ledger.advance(sale.expires)
        host.auction_ledger.enqueue_returned(a, "interrupted", cards[:1])
        tokens = {seat: host.economy.reserve_match(key) for seat, key in enumerate((a, b))}
        host.match_economy.begin("open-round", tokens, 50)
        owned = host.economy.account_view(a)
        before = deepcopy(host.snapshot())
        self.prohibit_writes()
        all_claims = claim_rows(host)
        claims = {row["kind"]: row for row in all_claims if row["account_key"] == a}
        amounts = lambda row: (row["prize_points"], row["money"], row["cards"])
        self.assertEqual(amounts(claims["Prize point award"]), (15, 0, 0))
        self.assertEqual(amounts(claims["Event grant"]), (0, 75, 2))
        self.assertEqual(amounts(claims["Auction payment"]), (0, 120, 0))
        self.assertEqual(amounts(claims["Returned card"]), (0, 0, 1))
        self.assertEqual(amounts(claims["Match escrow / unfinished settlement"]), (0, 50, 0))
        self.assertIn(
            (b, "Auction refund", 100, 0),
            [(row["account_key"], row["kind"], row["money"], row["cards"]) for row in all_claims],
        )
        self.assertIn(
            (c, "Auction purchase", 0, 1),
            [(row["account_key"], row["kind"], row["money"], row["cards"]) for row in all_claims],
        )
        detail = account_detail(host, a)
        self.assertEqual(
            (detail["balance"], detail["card_count"]), (owned.balance, len(owned.cards))
        )
        self.assertEqual(detail["prize_points"], 0)
        self.assertEqual(host.snapshot(), before)
        self.assertEqual(len({row["id"] for row in all_claims}), len(all_claims))

    def test_result_handoff_replaces_round_claim_without_double_counting(self):
        host = self.host
        a, b, _ = self.keys
        hands = {seat: host.economy.inventory(key)[:5] for seat, key in enumerate((a, b))}
        tokens = {seat: host.economy.reserve_match(key) for seat, key in enumerate((a, b))}
        tokens = host.match_economy.begin("completed-round", tokens, 50)
        extras = tuple(replace(card, deck=255) for card in hands[0][:2])
        tokens, _ = host.match_economy.record_result(
            "completed-round",
            tokens,
            scores=(7, 3),
            ratings=(300, 300),
            cbc={0: extras},
        )
        tokens, _ = host.match_economy.settle_cards(
            "completed-round",
            {seat: (tokens[seat], hand) for seat, hand in hands.items()},
            (),
        )
        before_owned = len(host.economy.inventory(a))
        host.match_economy.handoff("completed-round", tokens, host.result_rewards)
        rows = [row for row in claim_rows(host) if row["account_key"] == a]
        self.assertEqual(
            [(row["kind"], row["cards"]) for row in rows], [("Unaccepted match result", 2)]
        )
        self.assertEqual(account_detail(host, a)["card_count"], before_owned)
        host.result_rewards.commit(a)
        self.assertEqual([row for row in claim_rows(host) if row["account_key"] == a], [])
        self.assertEqual(account_detail(host, a)["card_count"], before_owned + 2)

    def test_rows_and_nested_account_details_are_detached(self):
        host = self.host
        key = self.keys[0]
        host.events.admit_room(key, "festival")
        host.events.update_progress(key, "festival", "step-1", steps_delta=1)
        host.prize.grant_award(key, "award", top30=7)
        before = deepcopy(host.snapshot())
        self.prohibit_writes()
        detail = account_detail(host, key)
        detail["inventory"][0]["attack"] = -100
        detail["events"][0]["steps"] = -100
        detail["pending_prize"]["top30"] = -100
        detail["pending_claims"][0]["prize_points"] = -100
        accounts = account_rows(host)
        accounts[0]["balance"] = -100
        rooms = room_rows(host)
        rooms[0]["name"] = "changed"
        self.assertEqual(host.snapshot(), before)
        self.assertEqual(account_detail(host, key)["pending_prize"]["top30"], 7)
        self.assertNotEqual(room_rows(host)[0]["name"], "changed")

    def test_room_and_event_occupancy_come_from_each_configured_owner(self):
        host = self.host
        a, b, _ = self.keys
        fixed = host.ordinary_room_bindings[0][1].room
        event = host.event_rooms.rooms[0].room
        owner = self.enter_room(fixed, a, "ordinary-peer")
        self.enter_room(event, b, "event-peer")
        fixed.tables[1].set_table(1, 1, owner.nn)
        event.tables[1].set_table(1, 2, host.economy.account_view(b).nn)
        self.prohibit_writes()
        rows = {row["name"]: row for row in room_rows(host)}
        self.assertEqual(
            (
                rows["Fixed rules"]["members"],
                rows["Fixed rules"]["tables"],
                rows["Fixed rules"]["occupied_tables"],
            ),
            (1, 2, 1),
        )
        self.assertTrue(rows["Fixed rules"]["computer_enabled"])
        self.assertIn("5 minute turns", rows["Fixed rules"]["rules"])
        self.assertIn("Special tiles", rows["Fixed rules"]["rules"])
        self.assertIn("Chance blocks", rows["Fixed rules"]["rules"])
        self.assertEqual(
            (
                rows["Festival"]["members"],
                rows["Festival"]["tables"],
                rows["Festival"]["occupied_tables"],
            ),
            (1, 2, 1),
        )
        self.assertFalse(rows["Festival"]["computer_enabled"])
        self.assertEqual(rows["Festival"]["event_id"], "festival")
        overview = server_overview(host, started_at=0)
        self.assertEqual(
            (overview["rooms"], overview["tables"], overview["occupied_tables"]), (3, 5, 2)
        )

    def test_overview_health_is_sanitized_and_clock_cannot_make_negative_uptime(self):
        host = self.host
        host.nicks[b"online"] = SimpleNamespace(closed=False)
        host.nicks[b"closed"] = SimpleNamespace(closed=True)
        self.prohibit_writes()
        with patch("tmserver.admin.queries.time.monotonic", return_value=100):
            healthy = server_overview(host, started_at=110)
        self.assertEqual((healthy["uptime_seconds"], healthy["connected_accounts"]), (0, 1))
        self.assertEqual(healthy["storage"], "Memory only")
        host.storage_failed = OSError("postgres://operator:secret@database/private")
        host.log.error = OSError("C:/private/credential.log")
        failed = server_overview(host, started_at=0)
        self.assertEqual(failed["storage"], "Writes stopped after a storage failure")
        self.assertEqual(failed["diagnostics"], "Logging stopped")
        self.assertNotIn("secret", json.dumps(failed))
        self.assertNotIn("credential.log", json.dumps(failed))
