"""Preview startup refunds and returned cards using independent restored state."""

from tmserver.auction_economy import AuctionEconomy
from tmserver.economy_service import EconomyService
from tmserver.match_economy import MatchEconomy
from tmserver.result_rewards import ResultRewardService


def preview_recovery(snapshot):
    """Run the existing startup rules in memory; never mutate the supplied save."""
    economy = EconomyService.restore(snapshot)
    auction = AuctionEconomy.restore(economy, snapshot.get("auction"))
    matches = MatchEconomy.restore(economy, snapshot.get("match_economy"))
    rewards = ResultRewardService.restore(economy, auction, snapshot.get("result_rewards"))

    def assets():
        return {
            key: (
                economy.balance(key),
                sum(item.kind == "returned" for item in auction.pending(key)),
            )
            for key in sorted(snapshot["accounts"])
        }

    before = assets()
    rounds = matches.recover_all(auction)
    results = rewards.recover_all()
    accounts = []
    for key, (balance, returned) in assets().items():
        old_balance, old_returned = before[key]
        if (balance, returned) != before[key]:
            accounts.append(
                dict(
                    account_key=key,
                    balance_before=old_balance,
                    balance_after=balance,
                    refunded_money=balance - old_balance,
                    returned_cards_added=returned - old_returned,
                    returned_cards_after_recovery=returned,
                )
            )
    return dict(interrupted_rounds=rounds, unaccepted_results=results, accounts=accounts)
