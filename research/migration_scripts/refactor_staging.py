"""One-time move of staging rules beside their owning services.

This migration is retained as review evidence, not a runtime dependency.
"""

from pathlib import Path
import textwrap

PACKAGE = Path(__file__).resolve().parents[1] / "src/tmserver"


def methods(filename, name, text, imports):
    path = PACKAGE / filename
    source = path.read_text()
    marker = f"class {name}:\n"
    assert source.count(marker) == 1, filename
    # Imports precede the class, after the module's other definitions if any.
    source = source.replace(
        marker,
        imports + "\n\n" + marker + textwrap.indent(textwrap.dedent(text).strip() + "\n\n", "    "),
        1,
    )
    path.write_text(source, encoding="utf8", newline="\n")


def main():
    methods(
        "auction_economy.py",
        "AuctionEconomy",
        '''
    def stage(self, economy, keys):
        """Isolate selected collection editors; retain their capability identity."""
        staged = self.stage_returns(economy)
        staged._listings = dict(self._listings)  # Frozen rows.
        staged._history = dict(self._history)  # Tuples of frozen bids.
        staged._collections = dict(self._collections)
        for key in set(keys).intersection(self._collections):
            staged._collections[key] = replace(self._collections[key])
        return staged

    def publish(self, staged):
        self.publish_returns(staged)
        self._listings = staged._listings
        self._history = staged._history
        self._next_id = staged._next_id
        self._collections = staged._collections

    def stage_returns(self, economy):
        """Match recovery appends returns without editing listings or collections."""
        staged = copy(self)
        staged.economy = economy
        staged._external_returns = dict(self._external_returns)
        return staged

    def publish_returns(self, staged):
        self._pending = staged._pending
        self._next_pending = staged._next_pending
        self._external_returns = staged._external_returns
    ''',
        "from copy import copy",
    )
    methods(
        "auction_service.py",
        "AuctionService",
        '''
    def stage(self, ledger, keys):
        """Stage wire sessions against the transaction's account/ledger view."""
        staged = copy(self)
        staged.ledger, staged.economy = ledger, ledger.economy
        staged._sessions = dict(self._sessions)
        staged._snapshots = dict(self._snapshots)
        staged._collections = dict(self._collections)
        for key in keys:
            if key in self._snapshots:
                staged._snapshots[key] = dict(self._snapshots[key])
            if key in self._collections:
                row = self._collections[key]
                staged._collections[key] = replace(row, cards=list(row.cards), names=dict(row.names))
        return staged

    def publish(self, staged):
        self._sessions = staged._sessions
        self._snapshots = staged._snapshots
        self._collections = staged._collections
        self._next_id = staged._next_id
    ''',
        "from copy import copy",
    )
    methods(
        "prize_service.py",
        "PrizeService",
        '''
    def stage(self, economy, key):
        """Keep draws and working purchases isolated until the joint save."""
        staged = copy(self)
        staged.economy = economy
        staged._rng = deepcopy(self._rng)
        staged._pending_awards = dict(self._pending_awards)
        staged._known_awards = dict(self._known_awards)
        staged._last = dict(self._last)
        staged._active = dict(self._active)
        staged._closed = dict(self._closed)
        if key in self._known_awards:
            staged._known_awards[key] = set(self._known_awards[key])
        if key in self._active:
            row = self._active[key]
            pending = row.pending
            if pending is not None:
                pending = replace(pending, cards=list(pending.cards), names=dict(pending.names))
            staged._active[key] = replace(row, pending=pending)
        return staged

    def publish(self, staged):
        self._rng = staged._rng
        self._pending_awards = staged._pending_awards
        self._known_awards = staged._known_awards
        self._last = staged._last
        self._active = staged._active
        self._closed = staged._closed
    ''',
        "from copy import copy, deepcopy",
    )
    methods(
        "event_service.py",
        "EventHost",
        '''
    def stage(self, economy):
        """Membership, progress facts and admissions are immutable map values."""
        staged = copy(self)
        staged.economy = economy
        staged._members = {event: dict(rows) for event, rows in self._members.items()}
        staged._updates = {event: dict(rows) for event, rows in self._updates.items()}
        staged._present = dict(self._present)
        staged._admissions = dict(self._admissions)
        return staged

    def publish(self, staged):
        self._members = staged._members
        self._updates = staged._updates
        self._present = staged._present
        self._admissions = staged._admissions
    ''',
        "from copy import copy",
    )
    methods(
        "event_cards.py",
        "EventCardService",
        '''
    def stage(self, economy, host, keys):
        """Copy claim offsets and editors only for participating accounts."""
        keys = frozenset(keys)
        staged = copy(self)
        staged.economy, staged.host = economy, host
        staged._grants = {
            identity: replace(row) if row.key in keys else row
            for identity, row in self._grants.items()
        }
        staged._last = dict(self._last)
        staged._closed = dict(self._closed)
        staged._active = dict(self._active)
        for key in keys.intersection(self._active):
            row = self._active[key]
            staged._active[key] = replace(row, cards=list(row.cards), names=dict(row.names))
        return staged

    def publish(self, staged):
        self._grants = staged._grants
        self._last = staged._last
        self._closed = staged._closed
        self._active = staged._active
    ''',
        "from copy import copy",
    )
    methods(
        "event_results.py",
        "EventResults",
        """
    def stage(self):
        staged = copy(self)
        staged._rows = dict(self._rows)  # Existing receipts are never edited.
        return staged

    def publish(self, staged):
        self._rows = staged._rows
    """,
        "from copy import copy",
    )
    methods(
        "match_economy.py",
        "MatchEconomy",
        """
    def stage(self, economy, keys):
        keys = frozenset(keys)
        staged = copy(self)
        staged.economy = economy
        staged._rounds = {
            identity: deepcopy(row) if keys.intersection(row.seats.values()) else row
            for identity, row in self._rounds.items()
        }
        return staged

    def publish(self, staged):
        self._rounds = staged._rounds
    """,
        "from copy import copy, deepcopy",
    )
    methods(
        "result_rewards.py",
        "ResultRewardService",
        """
    def stage(self, economy, auction, keys):
        staged = copy(self)
        staged.economy, staged.auction = economy, auction
        staged._known = dict(self._known)
        staged._accepted = dict(self._accepted)
        staged._pending = dict(self._pending)
        for key in set(keys).intersection(self._pending):
            row = self._pending[key]
            staged._pending[key] = replace(
                row,
                cards=None if row.cards is None else list(row.cards),
                names=None if row.names is None else dict(row.names),
            )
        return staged

    def publish(self, staged):
        self._known = staged._known
        self._pending = staged._pending
        self._accepted = staged._accepted
    """,
        "from copy import copy",
    )
    methods(
        "rating_policy.py",
        "RatingPolicy",
        """
    def stage(self, economy):
        # Commits replace validated history; they never mutate the shared history.
        staged = copy(self)
        staged.economy = economy
        return staged

    def publish(self, staged):
        self.history = staged.history
    """,
        "from copy import copy",
    )
    methods(
        "character_profiles.py",
        "CharacterProfiles",
        """
    def stage(self):
        # Retain the full read-only economy for cross-account public lookups.
        staged = copy(self)
        staged.records = dict(self.records)  # Immutable record tuples.
        return staged

    def publish(self, staged):
        self.records = staged.records
    """,
        "from copy import copy",
    )


if __name__ == "__main__":
    main()
