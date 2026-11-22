"""Synchronous account provisioning under the game's existing writer lease."""

from tmserver.network.local_accounts import display_name
from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import polid_from_string, polid_scramble, polid_to_string, polid_unscramble

# The client sends the raw POL ID as its GID, and the save and profile files hold
# that player ID in 32 bits. Registered POL IDs start above the fixture accounts
# and service raw IDs: EEEEFWIQ through EF5FOF5A.
MIN_REGISTERED_RAW = 1 << 16
MAX_REGISTERED_RAW = 0xFFFFFFFF


def provision_account(accounts, key, character, name=None):
    """Stage an account whose native identities derive from its key and character."""
    accounts.provision(key, nn=player_id(key.encode("ascii")), player_id=character,
                       name=display_name(character) if name is None else name)


def registered_key(host, pol_id):
    """The account key a POL ID would own, or None when it is reserved."""
    raw = polid_from_string(pol_id)
    key = polid_scramble(raw).decode("ascii")
    reserved = set()
    for routes in (host.ordinary_room_routes, host.ordinary_table_routes,
                   host.event_routes, host.event_matches.routes):
        reserved.update(nick.decode("ascii") for nick in routes)
    if not MIN_REGISTERED_RAW <= raw <= MAX_REGISTERED_RAW or key in reserved:
        return None
    return key


def register_account(host, key, credential):
    """Create one account and its password hash in a single commit."""
    if host.store is None:
        raise ValueError("account registration requires persistent storage")
    character = polid_unscramble(key.encode("ascii"))  # GID, as polcore derives it.
    staged = host.credentials.staged(key, credential)
    with host.economy.edit_accounts(()) as accounts:
        provision_account(accounts, key, character, polid_to_string(character).encode("ascii"))
        host.persist_changes(accounts.snapshot()["accounts"], {"identities": staged.snapshot()})
    host.credentials.publish_state(staged)
    host.account_keys.add(key)
