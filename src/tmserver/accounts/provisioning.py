"""Synchronous first-login provisioning under the game's existing writer lease."""

from tmserver.network.local_accounts import MAX_ACCOUNTS, display_name
from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import polid_scramble

from .identities import IdentityLink, check_native_identity


HOSTED_RAW_BASE = 1 << 40  # Separate from local fixtures and ordinary service IDs.


class AccountCapacityError(ValueError):
    """No remaining native character identifier can be allocated."""


def provision_account(accounts, key, character, name=None):
    """Stage an account whose native identities derive from its key and character."""
    accounts.provision(key, nn=player_id(key.encode("ascii")), player_id=character,
                       name=display_name(character) if name is None else name)


def import_bindings(host, issuer, bindings):
    """Persist explicit operator links without ever reassigning an existing owner."""
    if host.store is None:
        raise ValueError("hosted authentication requires persistent storage")
    links = [IdentityLink(issuer=issuer, subject=subject, account_key=key)
             for subject, key in bindings.items()]
    staged = host.identities.stage_links(links)
    if staged.snapshot() == host.identities.snapshot():
        return
    for link in links:
        if link.account_key in host.account_keys:
            check_native_identity(host.economy, link.account_key)
            if link.account_key.encode("ascii") in host.nicks or host.economy.is_busy(link.account_key):
                raise ValueError("disconnect the account before importing its identity binding")
    with host.economy.edit_accounts(()) as accounts:
        for link in links:
            if link.account_key not in host.account_keys:
                provision_account(accounts, link.account_key, host.account_specs[link.account_key][2])
        host.persist_changes(accounts.snapshot()["accounts"], {"identities": staged.snapshot()})
    host.identities.publish_state(staged)
    host.account_keys.update(link.account_key for link in links)


def provision_identity(host, issuer, subject, *, name=None):
    existing = host.identities.account(issuer, subject)
    if existing is not None:
        return existing
    if host.store is None:
        raise ValueError("hosted authentication requires persistent storage")
    character = max([MAX_ACCOUNTS, *(row["player_id"] for row in host.economy.snapshot()["accounts"].values())]) + 1
    reserved = set(host.account_specs) | host.account_keys
    for routes in (host.ordinary_room_routes, host.ordinary_table_routes,
                   host.event_routes, host.event_matches.routes):
        reserved.update(nick.decode("ascii") for nick in routes)
    while character <= 0xFFFFFFFF:
        key = polid_scramble(HOSTED_RAW_BASE + character).decode("ascii")
        if key not in reserved:
            break
        character += 1
    else:
        raise AccountCapacityError("native character IDs are exhausted")
    link = IdentityLink(issuer=issuer, subject=subject, account_key=key)
    identities = host.identities.stage_links((link,))
    with host.economy.edit_accounts(()) as accounts:
        provision_account(accounts, key, character,
                          name.encode("ascii") if name else f"Player{character}".encode("ascii")[:15])
        host.persist_changes(accounts.snapshot()["accounts"], {"identities": identities.snapshot()})
    host.identities.publish_state(identities)
    host.account_keys.add(key)
    host.register_hosted_account(key)
    return key
