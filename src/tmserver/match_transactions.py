"""Coordinate a match's account, settlement, reward and rating commit.

Each service owns the copies it needs; the coordinator owns commit ordering.
Existing service references and unrelated table capabilities remain stable.
Journals are still saved as complete documents. No context may span an await.
"""

from contextlib import contextmanager
from dataclasses import dataclass

from tmserver.auction_economy import AuctionEconomy
from tmserver.economy_service import EconomyService
from tmserver.match_economy import MatchEconomy
from tmserver.rating_policy import RatingPolicy
from tmserver.result_rewards import ResultRewardService


@dataclass
class MatchState:
    economy: EconomyService
    match_economy: MatchEconomy
    result_rewards: ResultRewardService
    ratings: RatingPolicy
    auction_ledger: AuctionEconomy

    @classmethod
    def stage(cls, host, economy, keys):
        auction = host.auction_ledger.stage_returns(economy)
        return cls(
            economy,
            host.match_economy.stage_state(economy, keys),
            host.result_rewards.stage_state(economy, auction, keys),
            host.ratings.stage_state(economy),
            auction,
        )

    def snapshot(self):
        return {
            "match_economy": self.match_economy.snapshot(),
            "result_rewards": self.result_rewards.snapshot(),
            "ratings": self.ratings.snapshot(),
            "auction": self.auction_ledger.snapshot(),
        }

    def publish(self, host):
        host.match_economy.publish_state(self.match_economy)
        host.result_rewards.publish_state(self.result_rewards)
        host.ratings.publish_state(self.ratings)
        host.auction_ledger.publish_returns(self.auction_ledger)


@contextmanager
def match_transaction(host, keys):
    keys = frozenset(keys)
    with host.economy.edit_accounts(keys) as economy:
        staged = MatchState.stage(host, economy, keys)
        yield staged
        host.persist_changes(economy.snapshot()["accounts"], staged.snapshot())
    staged.publish(host)
