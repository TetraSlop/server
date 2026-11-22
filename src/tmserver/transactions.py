"""One synchronous commit for accounts and the service journals they touch.

transaction(host, keys, *names) stages the keyed accounts and the named
services (plus the services they depend on), runs the body against the staged
copies, saves the accounts and journals together, then publishes. If the body
or the save raises, nothing is published. Never await inside the context:
account capabilities and random generators are process-local.
"""

from contextlib import contextmanager
from types import SimpleNamespace

# Host attribute: (journal name or None, {dependency attribute: staged source}).
# Dependencies come before the services that use them.
SERVICES = {
    "auction_ledger": ("auction", {"economy": "economy"}),
    "auction": (None, {"ledger": "auction_ledger", "economy": "economy"}),
    "prize": ("prize", {"economy": "economy"}),
    "match_economy": ("match_economy", {"economy": "economy"}),
    "result_rewards": ("result_rewards", {"economy": "economy", "auction": "auction_ledger"}),
    "ratings": ("ratings", {"economy": "economy"}),
    "events": ("events", {"economy": "economy"}),
    "event_cards": ("event_cards", {"economy": "economy", "host": "events"}),
    "event_results": ("event_results", {}),
    # Profiles keep the live economy: their reads cross accounts.
    "character_profiles": ("character_profiles", {}),
    "credentials": ("identities", {}),
    "admin_audit": ("admin_audit", {}),
}
EVENT_SERVICES = ("events", "event_cards", "event_results")
MATCH_SERVICES = ("match_economy", "result_rewards", "ratings")  # Stages auction_ledger too.


def _with_dependencies(names):
    wanted = set(names)
    if wanted - SERVICES.keys():
        raise ValueError(f"unknown staged services: {sorted(wanted - SERVICES.keys())}")
    for name in reversed(SERVICES):
        if name in wanted:
            wanted.update(source for source in SERVICES[name][1].values() if source != "economy")
    return [name for name in SERVICES if name in wanted]


@contextmanager
def transaction(host, keys=(), *names):
    """Yield tx with tx.economy and one staged copy per service.

    Pass every account the body reads through tx.economy. Set tx.save = False
    when the body has proved nothing changed; the staged state still publishes.
    """
    keys = frozenset(keys)
    staged_names = _with_dependencies(names)
    with host.economy.edit_accounts(keys) as economy:
        tx = SimpleNamespace(economy=economy, save=True)
        for name in staged_names:
            sources = SERVICES[name][1]
            bound = {attribute: getattr(tx, source) for attribute, source in sources.items()}
            setattr(tx, name, getattr(host, name).stage_copy(keys, **bound))
        yield tx
        if tx.save:
            host.persist_changes(
                economy.snapshot()["accounts"],
                {
                    SERVICES[name][0]: getattr(tx, name).snapshot()
                    for name in staged_names
                    if SERVICES[name][0] is not None
                },
            )
    for name in staged_names:
        getattr(host, name).publish_staged(getattr(tx, name))
