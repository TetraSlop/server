"""Rebuild every saved service from one snapshot, in dependency order.

The game server, offline administration and snapshot validation all restore
through here, so a journal is validated the same way wherever a save is read.
Restoring never runs startup recovery.
"""

import time
from dataclasses import dataclass

from tmserver.accounts.identities import IdentityRegistry
from tmserver.admin.audit import AdminAudit
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.prize_center import PrizeService
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.economy.service import EconomyService
from tmserver.events.cards import EventCardService
from tmserver.events.results import EventResults
from tmserver.events.service import EventHost
from tmserver.profiles.characters import CharacterProfiles
from tmserver.rankings.policy import RatingPolicy
from tmserver.storage.validation import validate_snapshot_fields

# Saved journal name -> RestoredState (and Server) attribute, in save order.
JOURNALS = (
    ("identities", "identities"),
    ("auction", "auction_ledger"),
    ("prize", "prize"),
    ("admin_audit", "admin_audit"),
    ("result_rewards", "result_rewards"),
    ("match_economy", "match_economy"),
    ("ratings", "ratings"),
    ("character_profiles", "character_profiles"),
    ("events", "events"),
    ("event_cards", "event_cards"),
    ("event_results", "event_results"),
)


@dataclass
class RestoredState:
    economy: EconomyService
    identities: IdentityRegistry
    auction_ledger: AuctionEconomy
    prize: PrizeService
    result_rewards: ResultRewardService
    match_economy: MatchEconomy
    ratings: RatingPolicy
    character_profiles: CharacterProfiles
    events: EventHost
    event_cards: EventCardService
    admin_audit: AdminAudit
    event_results: EventResults

    def snapshot(self):
        return dict(
            self.economy.snapshot(),
            **{name: getattr(self, attribute).snapshot() for name, attribute in JOURNALS},
        )


def restore_state(
    snapshot,
    *,
    event_configs=(),
    seed=None,
    shop_offers=None,
    rating_mode="preserve",
    weekly_rankings=False,
    ranking_clock=time.time,
    event_clock=time.time,
):
    """Restore snapshot, or start empty services when it is None."""
    if snapshot is None:
        economy = EconomyService(seed=seed, shop_offers=shop_offers)
        saved = {}
    else:
        validate_snapshot_fields(snapshot)
        economy = EconomyService.restore(snapshot, seed=seed, shop_offers=shop_offers)
        saved = snapshot
    # Same order as the game server has always restored, so the first invalid
    # journal reported does not change.
    identities = IdentityRegistry.restore(economy, saved.get("identities"))
    auction_ledger = AuctionEconomy.restore(economy, saved.get("auction"))
    prize = PrizeService.restore(economy, saved.get("prize"), seed=seed)
    result_rewards = ResultRewardService.restore(
        economy, auction_ledger, saved.get("result_rewards")
    )
    match_economy = MatchEconomy.restore(economy, saved.get("match_economy"))
    ratings = RatingPolicy(
        economy,
        mode=rating_mode,
        weekly=weekly_rankings,
        clock=ranking_clock,
        snapshot=saved.get("ratings"),
    )
    character_profiles = CharacterProfiles.restore(economy, saved.get("character_profiles"))
    events = EventHost.restore(economy, event_configs, saved.get("events"), clock=event_clock)
    event_cards = EventCardService.restore(economy, events, saved.get("event_cards"))
    admin_audit = AdminAudit.restore(
        economy, prize, saved.get("admin_audit"), event_cards=event_cards
    )
    event_results = EventResults.restore(saved.get("event_results"))
    return RestoredState(
        economy, identities, auction_ledger, prize, result_rewards, match_economy, ratings,
        character_profiles, events, event_cards, admin_audit, event_results,
    )
