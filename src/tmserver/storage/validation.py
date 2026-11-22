"""Validate saved domain state without database dependencies or startup recovery."""

# Journals a save may carry beside its accounts; PostgreSQL migrations freeze
# their own copy of this list.
SERVICE_NAMES = frozenset(
    {
        "auction",
        "prize",
        "admin_audit",
        "identities",
        "result_rewards",
        "match_economy",
        "ratings",
        "character_profiles",
        "events",
        "event_cards",
        "event_results",
    }
)
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
    """Run every journal's restore validator without startup recovery."""
    from tmserver.storage.restore import restore_state

    if snapshot is None:
        raise ValueError("saved economy and accounts must be objects")
    return restore_state(snapshot, event_configs=event_configs).economy
