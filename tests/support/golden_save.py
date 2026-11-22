"""Build the golden saved state through the server's own services.

The pinned documents change only when a save format change is intended.
Regenerate them with

    python -m tests.support.golden_save

TM_TEST_DATABASE_URL must name a disposable PostgreSQL server; the export is
produced in a temporary database that is dropped afterwards.
"""

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tests.economy.auctions.test_auction_service import exhibit_request
from tests.economy.test_claim_transactions import ClaimFixture
from tests.economy.test_match_transactions import Peer, TableFixture
from tests.events.test_event_rooms import config
from tests.profiles.test_character_profiles import request as profile_request
from tests.support.memory_store import MemoryStore
from tmserver.accounts.credentials import hash_password
from tmserver.accounts.provisioning import register_account
from tmserver.admin.actions import apply_event_grant, apply_event_member, apply_prize_award
from tmserver.admin.audit import (
    AdminAudit,
    EventGrantCard,
    EventGrantRequest,
    EventProgressRequest,
    PrizeAwardRequest,
)
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.claim_transactions import prize_transaction
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.packets import named
from tmserver.economy.prize_center import PrizeService
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.economy.service import EconomyService
from tmserver.events.cards import EventCardService
from tmserver.events.results import EventResults
from tmserver.events.service import EventHost
from tmserver.network.server import Server
from tmserver.profiles.characters import CharacterProfiles
from tmserver.profiles.transactions import character_profile_request
from tmserver.protocol.commands import Command
from tmserver.rankings.policy import RatingPolicy
from tmserver.protocol.wire import polid_from_string, polid_scramble
from tmserver.storage.restore import restore_state
from tmserver.storage.validation import validate_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "storage"
SNAPSHOT = FIXTURES / "golden_snapshot.json"
EXPORT = FIXTURES / "golden_postgres_export.json"
# A self-registered account; the server assigned it POL ID EEGOLDEN.
REGISTERED = polid_scramble(polid_from_string("EEGOLDEN")).decode("ascii")
EPOCH = 200  # Inside the fixture event's 100..500 window.
WEEK = 7 * 86400
COMMITTED_AT = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
EVENT = config(card_service_enabled=True)
ADMIN_CLOCKS = ("actions",)


def document(snapshot):
    """Serialize exactly as the saved-state exports write the document."""
    return json.dumps(snapshot, indent=2) + "\n"


def canonical(value):
    """Order-free comparison that still tells true from 1 and 1.0 from 1."""
    return json.dumps(value, sort_keys=True)


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return COMMITTED_AT


class GoldenRun(ClaimFixture, TableFixture):
    """One scripted server whose every saved journal holds realistic entries."""

    def __init__(self, store):
        self.now = EPOCH
        self.config = EVENT
        self.server = Server(
            store=store,
            account_count=3,
            match_mode="versus",
            card_prizes=True,
            allow_wagers=True,
            rating_mode="local-history",
            weekly_rankings=True,
            event_configs=(EVENT,),
            event_clock=lambda: self.now,
            auction_clock=lambda: self.now,
            ranking_clock=lambda: self.now,
        )
        self.keys = [self.server.ensure_account(nick) for nick in self.server.accounts]
        self.key, self.buyer, self.other = self.keys

    def run(self):
        clocks = [patch(f"tmserver.admin.{name}.datetime", _FixedClock) for name in ADMIN_CLOCKS]
        for clock in clocks:
            clock.start()
        try:
            register_account(self.server, REGISTERED,
                             hash_password("Hosted", b"golden", salt=bytes(16)))
            self.events()
            self.prizes()
            self.auctions()
            self.profiles()
            self.play_match()
        finally:
            for clock in clocks:
                clock.stop()
        return self.server

    def events(self):
        # A partly claimed grant, an audited grant, audited progress and a result.
        self.event_grant()
        number = self.enter_event()
        self.event(named(0xB2, 0x15, number, f"@Get=/No={number}"))
        self.event(quit_request(number))
        card = self.server.economy.inventory(self.other)[1]
        grant = EventGrantRequest(
            request_id="golden.grant", account_key=self.buyer, operator="golden",
            source="web", reason="golden save fixture", event_id="festival",
            grant_id="grant.audited", money=75,
            cards=(EventGrantCard(**{name: getattr(card, name) for name in EventGrantCard.model_fields}),),
        )
        apply_event_grant(self.server, grant)
        progress = EventProgressRequest(
            request_id="golden.progress", account_key=self.buyer, operator="golden",
            source="cli", reason="golden save fixture", event_id="festival",
            update_id="progress.audited", steps_delta=3,
        )
        apply_event_member(self.server, progress)
        self.record_result()

    def prizes(self):
        # An audited award that is redeemed, and a second award still pending.
        award = PrizeAwardRequest(
            request_id="golden.prize", account_key=self.key, operator="golden",
            source="cli", reason="golden save fixture", award_id="award.audited",
            top30=120, lucky=30, rookies=5, lucky_cards=(1, 2, 65535),
        )
        apply_prize_award(self.server, award)
        self.prize(quit_request(self.enter_prize()))
        with prize_transaction(self.server, self.buyer) as staged:
            staged.grant_award(self.buyer, "award.pending", lucky=40)

    def auctions(self):
        # A sold listing whose seller collected and whose won card is pending,
        # plus an open listing without bids.
        self.sell()
        self.auction(self.other, exhibit_request(self.server.economy, self.other, opening=250))
        number = self.collect(self.key)
        self.auction_get(self.key, number)
        self.auction_get(self.key, number)

    def profiles(self):
        view = self.server.economy.account_view(self.other)
        payload = profile_request(b"CR", view.nn, extras=(Command(b"AN", b"Golden"),))
        character_profile_request(self.server, self.other, payload)

    def play_match(self):
        # A wagered versus round with prizes, then a weekly ranking rollover.
        table = self.server.default_table
        self.peers = [Peer(key.encode()) for key in self.keys[:2]]
        players = []
        for peer in self.peers:
            token = self.server.economy.reserve_match(peer.nick.decode())
            table.match_reservations[peer] = token
            players.append(SimpleNamespace(
                peer=peer, updated=token.account.cards[:5], rating=300, departed=False
            ))
        self.match = SimpleNamespace(
            players=players, mode="versus", player_count=2, card_prizes=True, allow_wagers=True
        )
        table.match = self.match
        table.match_instance_id = "golden-table"  # Otherwise a random uuid4.
        self.begin()
        self.result()
        self.prepare_prize()
        table.commit_match_prizes(self.match)
        self.now += WEEK
        self.server.advance_rankings()


def quit_request(number):
    return named(0xB2, 0x17, number, f"@Quit=/No={number}")


def build(store=None):
    return GoldenRun(MemoryStore() if store is None else store).run()


def restore_document(snapshot):
    """Restore every journal without startup recovery, then snapshot it again."""
    return restore_state(
        snapshot,
        event_configs=(EVENT,),
        rating_mode="local-history",
        weekly_rankings=True,
        ranking_clock=lambda: EPOCH + WEEK,
        event_clock=lambda: EPOCH,
    ).snapshot()


def postgres_export(store, snapshot):
    """Save a validated snapshot into an empty upgraded store; return its SQL reload."""
    if store.load() is not None:
        raise ValueError("the golden export needs an empty database")
    validate_snapshot(snapshot, event_configs=(EVENT,))
    store.save(snapshot)
    return document(store.load())


def main():
    snapshot = build().snapshot()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_bytes(document(snapshot).encode("utf8"))
    url = os.environ.get("TM_TEST_DATABASE_URL")
    if not url:
        print(f"Wrote {SNAPSHOT}; set TM_TEST_DATABASE_URL to rewrite {EXPORT}.")
        return
    from tmserver.storage.database import upgrade
    from tmserver.storage.postgres import PostgresStateStore, database_engine

    name = "tm_golden_" + uuid4().hex
    admin = database_engine(url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        target = admin.url.set(database=name)
        upgrade(target)
        with PostgresStateStore(target) as store:
            EXPORT.write_bytes(postgres_export(store, snapshot).encode("utf8"))
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
    print(f"Wrote {SNAPSHOT} and {EXPORT}.")


if __name__ == "__main__":
    main()
