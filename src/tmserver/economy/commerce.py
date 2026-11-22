"""Account-only commits for shop, trade, profile and deck updates.

Commerce stands in for EconomyService where a caller needs the same methods
saved: TradeService and EventMatchmaking take either. Each method runs in one
transaction(); the caller sends protocol replies only after it returns.
"""

from contextlib import contextmanager

from tmserver.transactions import transaction


class Commerce:
    def __init__(self, host):
        self.host = host

    @contextmanager
    def edit_account(self, key):
        """Stage profile/preferences using the same one-account save boundary."""
        with transaction(self.host, (key,)) as tx:
            yield tx.economy

    def initialize_profile(self, key, payload):
        """Init probes and retries only save when they select a new guild."""
        with transaction(self.host, (key,)) as tx:
            guild = tx.economy.account_view(key).guild
            reply = tx.economy.initialize_profile(key, payload)
            tx.save = tx.economy.account_view(key).guild != guild
            return reply

    def shop(self, key, payload):
        with transaction(self.host, (key,)) as tx:
            replies = tx.economy.handle(key, payload)
            tx.save = replies is not None
            return replies

    def commit_exchange(self, reservation, cards_by_key, deck_names_by_key=None):
        keys = tuple(view.key for view in reservation.accounts)
        with transaction(self.host, keys) as tx:
            tx.economy.commit_exchange(reservation, cards_by_key, deck_names_by_key)

    def apply_deck_changes(self, key, cards, names=None):
        with transaction(self.host, (key,)) as tx:
            tx.economy.apply_deck_changes(key, cards, names)

    def apply_match_deck_changes(self, reservation, cards, names=None):
        with transaction(self.host, (reservation.account.key,)) as tx:
            return tx.economy.apply_match_deck_changes(reservation, cards, names)
