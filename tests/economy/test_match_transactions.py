"""Failure isolation at the ordinary-table account/journal commit boundary."""

import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tmserver.game.prizes import PrizeAward
from tmserver.economy.match_transactions import match_transaction
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import Server


class Peer:
    closed = False

    def __init__(self, nick):
        self.nick = nick


class TableFixture:
    def prepare_table(self, store=None):
        self.server = Server(
            store=store,
            account_count=3,
            rating_mode="local-history",
            card_prizes=True,
            allow_wagers=True,
            match_mode="versus",
        )
        self.keys = [self.server.ensure_account(nick) for nick in self.server.accounts]
        self.peers = [Peer(key.encode()) for key in self.keys[:2]]
        players = []
        for peer in self.peers:
            key = peer.nick.decode()
            token = self.server.economy.reserve_match(key)
            self.server.default_table.match_reservations[peer] = token
            players.append(
                SimpleNamespace(
                    peer=peer, updated=token.account.cards[:5], rating=300, departed=False
                )
            )
        self.match = SimpleNamespace(
            players=players, mode="versus", player_count=2, card_prizes=True, allow_wagers=True
        )
        self.server.default_table.match = self.match
        self.server.persist()

    def begin(self):
        return self.server.default_table.authorize_match_round(self.match, 1, 100)

    def prepare_result(self):
        scores = (7, 3)
        payouts = self.server.default_table.quote_match_result(self.match, 1, scores, (300, 300))
        changes = self.server.default_table.quote_match_rating(self.match, 1, scores)
        results = {}
        for seat, peer in enumerate(self.peers):
            key = peer.nick.decode()
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            cards = self.match.players[seat].updated
            results[peer] = SimpleNamespace(
                round_id=1,
                original=cards,
                updated=cards,
                stats=stats.next_result(
                    1 if seat == 0 else -1,
                    payouts[seat],
                    rating_change=changes[seat],
                    vs_player_game=True,
                ),
                extras=(replace(cards[0], deck=255),) if seat == 0 else (),
            )
        self.match.final_cards = results.get
        self.match.observer_snapshot = lambda: SimpleNamespace(scores=scores)

    def result(self):
        self.prepare_result()
        self.server.default_table.commit_match_results(self.match)

    def prepare_prize(self):
        source = self.match.players[1].updated[0]
        award = PrizeAward(0, 1, 0, 0, source, replace(source, deck=255))
        self.match.prize_commit_required = True
        self.match.final_prizes = lambda: SimpleNamespace(
            round_id=1, settlement=SimpleNamespace(transfers=(award,))
        )
        self.match.confirm_prize_commit = Mock(return_value=[])
        return award


class MatchTransactionTests(TableFixture, unittest.TestCase):
    def setUp(self):
        self.prepare_table()

    def assert_tokens(self, original):
        self.assertEqual(self.server.default_table.match_reservations, original)
        for token in original.values():
            self.assertIs(self.server.economy._reservations[token.account.key], token)

    def test_stake_save_observes_original_memory_then_publishes_all_participants(self):
        before = self.server.snapshot()
        untouched = self.server.economy._accounts[self.keys[2]]

        def save(accounts, journals):
            self.assertEqual(self.server.snapshot(), before)
            self.assertEqual(set(accounts), set(self.keys[:2]))
            self.assertEqual([a["balance"] for a in accounts.values()], [9900, 9900])
            self.assertEqual(journals["match_economy"]["rounds"][0]["money"], "escrow")
            self.assertIsNone(self.server.default_table.match_economy_round)

        with patch.object(self.server, "persist_changes", side_effect=save) as write:
            self.assertEqual(self.begin(), {0: 9900, 1: 9900})
        write.assert_called_once()
        self.assertIs(self.server.economy._accounts[self.keys[2]], untouched)

    def test_failed_stake_retains_accounts_journals_and_table_capabilities(self):
        before = self.server.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        with (
            patch.object(self.server, "persist_changes", side_effect=OSError("stake write")),
            self.assertRaises(OSError),
        ):
            self.begin()
        self.assertEqual(self.server.snapshot(), before)
        self.assertIsNone(self.server.default_table.match_economy_round)
        self.assert_tokens(tokens)

    def test_failed_result_retains_profiles_ratings_money_and_commit_marks(self):
        self.begin()
        self.prepare_result()
        before = self.server.snapshot()
        tokens = dict(self.server.default_table.match_reservations)

        def fail(accounts, journals):
            self.assertEqual(self.server.snapshot(), before)
            self.assertEqual(journals["match_economy"]["rounds"][0]["money"], "paid")
            self.assertTrue(
                all(a["results"] for a in journals["ratings"]["history"]["accounts"].values())
            )
            raise OSError("result write")

        with (
            patch.object(self.server, "persist_changes", side_effect=fail),
            self.assertRaises(OSError),
        ):
            self.server.default_table.commit_match_results(self.match)
        self.assertEqual(self.server.snapshot(), before)
        self.assert_tokens(tokens)
        self.assertFalse(self.server.default_table.match_committed)

    def test_prize_failure_cannot_lose_cards_or_publish_reward_entitlements(self):
        self.begin()
        self.result()
        self.prepare_prize()
        before = self.server.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        with (
            patch.object(self.server, "persist_changes", side_effect=OSError("prize write")),
            self.assertRaises(OSError),
        ):
            self.server.default_table.commit_match_prizes(self.match)
        self.match.confirm_prize_commit.assert_not_called()
        self.assertEqual(self.server.snapshot(), before)
        self.assert_tokens(tokens)

    def test_prize_commit_and_continue_keep_unrelated_live_editor_and_reservation(self):
        other = self.keys[2]
        token = self.server.economy.reserve_match(other)
        card = replace(token.account.cards[0], deck=255)
        self.server.result_rewards.stage(other, token, "other:1", 0, (card,))
        editor = self.server.result_rewards._pending[other]
        self.begin()
        self.result()
        self.prepare_prize()
        self.server.default_table.commit_match_prizes(self.match)
        self.assertEqual(len(self.server.economy.inventory(self.keys[1])), 9)
        self.assertEqual(self.server.result_rewards.status(self.keys[0])["extra_count"], 2)
        with match_transaction(self.server, (self.keys[0],)) as state:
            state.result_rewards.commit(self.keys[0])
        self.assertEqual(len(self.server.economy.inventory(self.keys[0])), 12)
        self.assertIs(self.server.result_rewards._pending[other], editor)
        self.assertIs(self.server.result_rewards.reservation(other), token)
        self.assertIs(self.server.economy._reservations[other], token)

    def test_pending_edits_and_accepted_contexts_are_discarded_on_failed_continue(self):
        self.begin()
        self.result()
        self.prepare_prize()
        self.server.default_table.commit_match_prizes(self.match)
        key = self.keys[0]
        pending = self.server.result_rewards._pending[key]
        before = deepcopy(pending.cards), self.server.snapshot()
        with (
            patch.object(self.server, "persist_changes", side_effect=OSError("Continue")),
            self.assertRaises(OSError),
            match_transaction(self.server, (key,)) as state,
        ):
            state.result_rewards._pending[key].cards.pop()
            state.result_rewards.commit(key)
        self.assertIs(self.server.result_rewards._pending[key], pending)
        self.assertEqual((pending.cards, self.server.snapshot()), before)
        self.assertNotIn(key, self.server.result_rewards._accepted)

    def test_abort_failure_does_not_publish_refund_or_release_table(self):
        self.begin()
        before = self.server.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        with (
            patch.object(self.server, "persist_changes", side_effect=OSError("refund write")),
            self.assertRaises(OSError),
        ):
            self.server.default_table.release_match_accounts()
        self.assertEqual(self.server.snapshot(), before)
        self.assert_tokens(tokens)
        self.server.default_table.release_match_accounts()
        self.assertEqual([self.server.economy.balance(k) for k in self.keys], [10000] * 3)
        self.assertFalse(self.server.default_table.match_reservations)

    def test_lobby_cleanup_releases_capabilities_after_roster_removal(self):
        self.match.players.clear()
        self.server.default_table.release_match_accounts()
        self.assertFalse(self.server.default_table.match_reservations)
        for key in self.keys:
            self.assertFalse(self.server.economy.is_reserved(key))

    def test_recovery_failure_keeps_pending_awards_and_does_not_enqueue_returns(self):
        self.begin()
        self.result()
        self.prepare_prize()
        self.server.default_table.commit_match_prizes(self.match)
        before = self.server.snapshot()
        with (
            patch.object(self.server, "persist_changes", side_effect=OSError("recovery write")),
            self.assertRaises(OSError),
        ):
            self.server.default_table.release_match_accounts()
        self.assertEqual(self.server.snapshot(), before)
        self.server.default_table.release_match_accounts()
        self.assertEqual(len(self.server.auction_ledger.pending(self.keys[0])), 2)
        committed = self.server.snapshot()
        self.server.default_table.release_match_accounts()
        self.assertEqual(self.server.snapshot(), committed)

