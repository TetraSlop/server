"""Validate saved domain state without database dependencies or startup recovery."""

from tmserver.state_store import SERVICE_NAMES

ACCOUNT_FIELDS = frozenset(
    {
        "nn",
        "player_id",
        "name_hex",
        "guild",
        "balance",
        "cards",
        "deck_names",
        "template_hex",
        "last_session",
    }
)


def validate_snapshot(snapshot, *, event_configs=()):
    """Use the domain's restore validators without running startup recovery."""
    from tmserver.auction_economy import AuctionEconomy
    from tmserver.character_profiles import CharacterProfiles
    from tmserver.economy_service import EconomyService
    from tmserver.event_cards import EventCardService
    from tmserver.event_results import EventResults
    from tmserver.event_service import EventHost
    from tmserver.match_economy import MatchEconomy
    from tmserver.prize_service import PrizeService
    from tmserver.rating_policy import RatingPolicy
    from tmserver.result_rewards import ResultRewardService

    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("accounts"), dict):
        raise ValueError("saved economy and accounts must be objects")
    if set(snapshot) - (SERVICE_NAMES | {"version", "accounts"}):
        raise ValueError("unknown snapshot sections; refusing to discard them")
    for account in snapshot["accounts"].values():
        if not isinstance(account, dict) or set(account) != ACCOUNT_FIELDS:
            raise ValueError("unsupported account fields")
    for name in SERVICE_NAMES:
        if snapshot.get(name) is not None and not isinstance(snapshot[name], dict):
            raise ValueError(f"saved {name} journal must be an object or null")
    economy = EconomyService.restore(snapshot)
    auctions = AuctionEconomy.restore(economy, snapshot.get("auction"))
    PrizeService.restore(economy, snapshot.get("prize"))
    ResultRewardService.restore(economy, auctions, snapshot.get("result_rewards"))
    MatchEconomy.restore(economy, snapshot.get("match_economy"))
    RatingPolicy(economy, snapshot=snapshot.get("ratings"))
    CharacterProfiles.restore(economy, snapshot.get("character_profiles"))
    events = EventHost.restore(economy, event_configs, snapshot.get("events"))
    EventCardService.restore(economy, events, snapshot.get("event_cards"))
    EventResults.restore(snapshot.get("event_results"))
    return economy
