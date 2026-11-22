"""Synchronous account provisioning under the game's existing writer lease."""

import secrets

from tmserver.network.local_accounts import display_name
from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import polid_scramble, polid_unscramble

# The client sends the raw POL ID as its GID, and the save and profile files hold
# that player ID in 32 bits. polcore also writes it as a signed 32-bit decimal in
# the GTM0P <CR> locator, so it stays below 2^31. Assigned POL IDs start above the
# fixture accounts and service raw IDs: EEEEFWIQ through EE5PIE5C.
MIN_REGISTERED_RAW = 1 << 16
MAX_REGISTERED_RAW = 0x7FFFFFFF


def provision_account(accounts, key, character, name=None):
    """Stage an account whose native identities derive from its key and character."""
    accounts.provision(key, nn=player_id(key.encode("ascii")), player_id=character,
                       name=display_name(character) if name is None else name)


def free_key(host):
    """A random unused account key whose POL ID is in the registrable range."""
    reserved = set(host.account_keys) | set(host.account_specs)
    for routes in (host.ordinary_room_routes, host.ordinary_table_routes,
                   host.event_routes, host.event_matches.routes):
        reserved.update(nick.decode("ascii") for nick in routes)
    while True:  # About 4 billion values; collisions stay rare for any real save.
        raw = MIN_REGISTERED_RAW + secrets.randbelow(MAX_REGISTERED_RAW - MIN_REGISTERED_RAW + 1)
        key = polid_scramble(raw).decode("ascii")
        if key not in reserved:
            return key


def register_account(host, key, credential):
    """Create one account and its username and password hash in a single commit."""
    if host.store is None:
        raise ValueError("account registration requires persistent storage")
    character = polid_unscramble(key.encode("ascii"))  # GID, as polcore derives it.
    staged = host.credentials.staged(key, credential)
    with host.economy.edit_accounts(()) as accounts:
        provision_account(accounts, key, character, credential.username.encode("ascii"))
        host.persist_changes(accounts.snapshot()["accounts"], {"identities": staged.snapshot()})
    host.credentials.publish_state(staged)
    host.account_keys.add(key)
