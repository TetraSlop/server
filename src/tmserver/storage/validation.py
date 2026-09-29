"""Validate saved domain state without database dependencies or startup recovery."""

from tmserver.storage.base import SERVICE_NAMES

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


def validate_snapshot_fields(snapshot):
    """Reject fields that this version would silently drop when saving again."""
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


def validate_snapshot(snapshot, *, event_configs=()):
    """Use the domain's restore validators without running startup recovery."""
    from tmserver.admin.audit import AdminAudit
    from tmserver.accounts.identities import IdentityRegistry
    from tmserver.economy.auctions.ledger import AuctionEconomy
    from tmserver.profiles.characters import CharacterProfiles
    from tmserver.economy.service import EconomyService
    from tmserver.events.cards import EventCardService
    from tmserver.events.results import EventResults
    from tmserver.events.service import EventHost
    from tmserver.economy.match_ledger import MatchEconomy
    from tmserver.economy.prize_center import PrizeService
    from tmserver.rankings.policy import RatingPolicy
    from tmserver.economy.result_rewards import ResultRewardService

    validate_snapshot_fields(snapshot)
    economy = EconomyService.restore(snapshot)
    IdentityRegistry.restore(economy, snapshot.get("identities"))
    auctions = AuctionEconomy.restore(economy, snapshot.get("auction"))
    prize = PrizeService.restore(economy, snapshot.get("prize"))
    ResultRewardService.restore(economy, auctions, snapshot.get("result_rewards"))
    MatchEconomy.restore(economy, snapshot.get("match_economy"))
    RatingPolicy(economy, snapshot=snapshot.get("ratings"))
    CharacterProfiles.restore(economy, snapshot.get("character_profiles"))
    events = EventHost.restore(economy, event_configs, snapshot.get("events"))
    event_cards = EventCardService.restore(economy, events, snapshot.get("event_cards"))
    AdminAudit.restore(economy, prize, snapshot.get("admin_audit"), event_cards=event_cards)
    EventResults.restore(snapshot.get("event_results"))
    return economy
