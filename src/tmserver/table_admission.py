"""Native entry restrictions and compatible nonfatal rejection replies."""

from tmserver.card_catalog import collection_points
from tmserver.match_service import packet


def eligibility_failure(account, *, wager=0, point_bounds=None):
    """Check committed inventory/money, not client-supplied profile fields.

    Callers supply only bounds used by the relevant native entry path. This
    does not check passwords: the original request contains no password proof.
    """
    if len(account.cards) < 5:
        return "insufficient_cards"
    if account.balance < wager:
        return "insufficient_money"
    if point_bounds is not None:
        lower, upper = point_bounds
        if not lower <= collection_points(account.cards) <= upper:
            return "collection_points"
    return None


def rejected(section, *, full):
    # -32870 maps to the client's table-full text for GameEA and PLAYACK.
    # GameECA has a separate not-ready branch at -32871. PLAYACK -32869
    # is the generic start failure; -32872 specifically reports cancellation.
    if section == "Req":
        return packet(2, body=f"@PLAYACK=/EN={-32870 if full else -32869}")
    if section == "GameEN":
        return packet(65, 2, body=f"@GameEA=/EN={-32870 if full else -32871}")
    if section == "GameENC":
        return packet(65, 19, body="@GameECA=/EN=-32871")
    raise ValueError("not a table admission section")
