"""Build the golden saved state through the server's own services.

The pinned documents change only when a save format change is intended.
Regenerate them with

    python -m tests.support.golden_save

TM_TEST_DATABASE_URL must name a disposable PostgreSQL server; the export is
produced in a temporary database that is dropped afterwards.
"""

import json
import os
import tempfile
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
from tmserver.accounts.identities import IdentityRegistry
from tmserver.accounts.provisioning import provision_identity
from tmserver.admin.actions import apply_prize_award, preview_prize_award
from tmserver.admin.audit import (
    AdminAudit,
    EventGrantCard,
    EventGrantRequest,
    EventProgressRequest,
    PrizeAwardRequest,
)
from tmserver.admin.event_actions import apply_event_grant, preview_event_grant
from tmserver.admin.member_actions import apply_event_member, preview_event_member
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
from tmserver.storage.validation import validate_snapshot_fields

FIXTURES = Path(__file__).parents[1] / "fixtures" / "storage"
SNAPSHOT = FIXTURES / "golden_snapshot.json"
EXPORT = FIXTURES / "golden_postgres_export.json"
ISSUER = "https://example.supabase.co/auth/v1"
SUBJECT = "00000000-0000-4000-8000-000000000001"
EPOCH = 200  # Inside the fixture event's 100..500 window.
WEEK = 7 * 86400
COMMITTED_AT = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
EVENT = config(card_service_enabled=True)
ADMIN_CLOCKS = ("actions", "event_actions", "member_actions")


def document(snapshot):
    """Serialize exactly as the saved-state exports write the document."""
    return json.dumps(snapshot, indent=2) + "\n"


def canonical(value):
    """Order-free comparison that still tells true from 1 and 1.0 from 1."""
    return json.dumps(value, sort_keys=True)


class MemoryStore:
    """Persistent store stand-in. The golden pins the document, not a file format."""

    def __init__(self, snapshot=None):
        self.snapshot = deepcopy(snapshot)

    def load(self):
        return deepcopy(self.snapshot)

    def save(self, snapshot):
        self.snapshot = deepcopy(snapshot)

    def save_changes(self, accounts, services):
        snapshot = self.snapshot or {"version": 1, "accounts": {}}
        snapshot["accounts"].update(deepcopy(accounts))
        snapshot.update(deepcopy(services))
        self.snapshot = snapshot


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
            provision_identity(self.server, ISSUER, SUBJECT, name="Hosted")
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
        apply_event_grant(self.server, grant, preview_event_grant(self.server, grant).digest)
        progress = EventProgressRequest(
            request_id="golden.progress", account_key=self.buyer, operator="golden",
            source="cli", reason="golden save fixture", event_id="festival",
            update_id="progress.audited", steps_delta=3,
        )
        apply_event_member(self.server, progress, preview_event_member(self.server, progress).digest)
        self.record_result()

    def prizes(self):
        # An audited award that is redeemed, and a second award still pending.
        award = PrizeAwardRequest(
            request_id="golden.prize", account_key=self.key, operator="golden",
            source="cli", reason="golden save fixture", award_id="award.audited",
            top30=120, lucky=30, rookies=5, lucky_cards=(1, 2, 65535),
        )
        apply_prize_award(self.server, award, preview_prize_award(self.server, award).digest)
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
    validate_snapshot_fields(snapshot)
    economy = EconomyService.restore(snapshot)
    identities = IdentityRegistry.restore(economy, snapshot.get("identities"))
    auction = AuctionEconomy.restore(economy, snapshot.get("auction"))
    prize = PrizeService.restore(economy, snapshot.get("prize"))
    rewards = ResultRewardService.restore(economy, auction, snapshot.get("result_rewards"))
    rounds = MatchEconomy.restore(economy, snapshot.get("match_economy"))
    ratings = RatingPolicy(
        economy, mode="local-history", weekly=True, clock=lambda: EPOCH + WEEK,
        snapshot=snapshot.get("ratings"),
    )
    profiles = CharacterProfiles.restore(economy, snapshot.get("character_profiles"))
    events = EventHost.restore(economy, (EVENT,), snapshot.get("events"), clock=lambda: EPOCH)
    cards = EventCardService.restore(economy, events, snapshot.get("event_cards"))
    audit = AdminAudit.restore(economy, prize, snapshot.get("admin_audit"), event_cards=cards)
    # Same section order as Server.snapshot().
    return dict(
        economy.snapshot(),
        identities=identities.snapshot(),
        auction=auction.snapshot(),
        prize=prize.snapshot(),
        admin_audit=audit.snapshot(),
        result_rewards=rewards.snapshot(),
        match_economy=rounds.snapshot(),
        ratings=ratings.snapshot(),
        character_profiles=profiles.snapshot(),
        events=events.snapshot(),
        event_cards=cards.snapshot(),
        event_results=EventResults.restore(snapshot.get("event_results")).snapshot(),
    )


def postgres_export(store, snapshot):
    """Import a snapshot into an empty upgraded store and return the export text."""
    from tmserver.storage.database import export_json, import_json

    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder) / "source.json", Path(folder) / "export.json"
        source.write_text(document(snapshot), encoding="utf8", newline="\n")
        import_json(store, source, event_configs=(EVENT,))
        export_json(store, target)
        # export_json writes the platform newline; the document itself uses LF.
        return target.read_text(encoding="utf8")


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
