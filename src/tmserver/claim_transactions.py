"""Synchronous joint commits for auctions, Prize Center and event claims.

Services own their staging rules. These contexts coordinate the affected accounts,
save their journals together, then publish into existing service objects. Never
await inside a context: account capabilities and RNG ownership are process-local.
"""

from contextlib import contextmanager
from dataclasses import dataclass

from tmserver.event_cards import EventCardService
from tmserver.event_results import EventResults
from tmserver.event_service import EventHost


@contextmanager
def auction_transaction(host, keys=()):
    keys = frozenset(keys)
    with host.economy.edit_accounts(keys) as economy:
        ledger = host.auction_ledger.stage_state(economy, keys)
        auction = host.auction.stage_state(ledger, keys)
        yield auction
        host.persist_changes(economy.snapshot()["accounts"], {"auction": ledger.snapshot()})
    host.auction_ledger.publish_state(ledger)
    host.auction.publish_state(auction)


@contextmanager
def prize_transaction(host, key):
    with host.economy.edit_accounts((key,)) as economy:
        prize = host.prize.stage_state(economy, key)
        yield prize
        host.persist_changes(economy.snapshot()["accounts"], {"prize": prize.snapshot()})
    host.prize.publish_state(prize)


@dataclass
class EventState:
    events: EventHost
    cards: EventCardService
    results: EventResults


@contextmanager
def event_transaction(host, keys):
    keys = frozenset(keys)
    with host.economy.edit_accounts(keys) as economy:
        events = host.events.stage_state(economy)
        cards = host.event_cards.stage_state(economy, events, keys)
        results = host.event_results.stage_state()
        yield EventState(events, cards, results)
        host.persist_changes(
            economy.snapshot()["accounts"],
            {
                "events": events.snapshot(),
                "event_cards": cards.snapshot(),
                "event_results": results.snapshot(),
            },
        )
    host.events.publish_state(events)
    host.event_cards.publish_state(cards)
    host.event_results.publish_state(results)
