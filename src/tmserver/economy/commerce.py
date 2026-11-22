"""Scoped account transactions for shop, trade, profile and deck updates.

Validate and change isolated accounts, commit them, then publish live state.
The caller sends protocol replies only after these synchronous operations return.
"""

from contextlib import contextmanager


class Commerce:
    def __init__(self, economy, persist_changes):
        self.economy = economy
        self.persist_changes = persist_changes

    @contextmanager
    def edit_account(self, key):
        """Stage profile/preferences using the same one-account save boundary."""
        with self.economy.edit_accounts((key,)) as staged:
            yield staged
            self.persist_changes(staged.snapshot()["accounts"], {})

    def initialize_profile(self, key, payload):
        """Init probes and retries only save when they select a new guild."""
        with self.economy.edit_accounts((key,)) as staged:
            guild = staged.account_view(key).guild
            reply = staged.initialize_profile(key, payload)
            if staged.account_view(key).guild != guild:
                self.persist_changes(staged.snapshot()["accounts"], {})
            return reply

    def shop(self, key, payload):
        with self.economy.edit_accounts((key,)) as staged:
            replies = staged.handle(key, payload)
            if replies is not None:
                self.persist_changes(staged.snapshot()["accounts"], {})
            return replies

    def commit_exchange(self, reservation, cards_by_key, deck_names_by_key=None):
        keys = tuple(view.key for view in reservation.accounts)
        with self.economy.edit_accounts(keys) as staged:
            staged.commit_exchange(reservation, cards_by_key, deck_names_by_key)
            self.persist_changes(staged.snapshot()["accounts"], {})

    def apply_deck_changes(self, key, cards, names=None):
        with self.economy.edit_accounts((key,)) as staged:
            staged.apply_deck_changes(key, cards, names)
            self.persist_changes(staged.snapshot()["accounts"], {})

    def apply_match_deck_changes(self, reservation, cards, names=None):
        with self.economy.edit_accounts((reservation.account.key,)) as staged:
            token = staged.apply_match_deck_changes(reservation, cards, names)
            self.persist_changes(staged.snapshot()["accounts"], {})
        return token
