# Code ownership

`run_server` constructs the two loopback listeners; `server` owns connection
lifetime and dispatch. `pol_wire`, `tm_protocol` and `profile_service` implement
wire formats. The named services own game behavior and state. `native_rules`,
`native_ai` and `native_board_generation` contain executable-derived algorithms;
reward and rating services document their explicit local policies separately.

The connection router handles service requests before peer delivery. Native game
messages sent between players must be normalized chat or validated direct trade
controls. Original control receivers can adopt an arbitrary sender, and their
lower receive path ignores the fifth service-marker byte; generic `GTM0` relays
would therefore let a player impersonate service controls. Server-owned replies
use their normal service delivery paths. Opaque non-game relay traffic is retained.

The model stays ordinary Python dataclasses. SQLAlchemy Core and Alembic are
optional persistence dependencies. There is no second ORM or Pydantic domain
model to keep synchronized with the protocol's native representations.

`cards.Card` defines the eight stored card fields once. Native rules and matches
keep thin adapters for their different validation domains and wire encodings;
`Card.from_card` converts through the receiving constructor. Economy imports
continue to expose the shared value for compatibility.

`card_catalog` owns decoded card data, card generation, prices and collection
points. Matches and admission rules import it directly without depending on
account/shop handling. `economy_errors` defines the shared rejection type;
`economy_service` re-exports it and the catalogue API for existing callers.
Catalogue construction loads packaged data or an explicit JSON path; matches
also accept an injected catalogue. Neither operation creates accounts, loads
saves or runs a service.

`inventory_edits` parses numbered card records, matches owned copies in stable
order and produces edited inventories without mutating its input. Shop, prize,
event, auction, trade and result services retain header/seat validation, numeric
and name dialects, pending state and commit timing. Offered trade copies cannot
be selected for editing, but assigning their deck slot still clears that slot
throughout the inventory. These differences are explicit at the call sites.

## Table and server lifetimes

`Server` owns shared account services, connections, room routing and auction
maintenance. Every ordinary table is an `OrdinaryTable`, including
`server.default_table`; the server does not inherit or forward table methods.
The default room remains `server.room`, the same object used by the default
table. Route maps and peer/channel lookups always return a table instance.

Each table owns its current match, reservation tokens, lock, roster, departure
worker and observer queues. Reset replaces that table's match and roster while
preserving the table's identity and its live references to shared services.
Tables in the same room share only the intended room membership and listing
state. Match callbacks remain bound to their table. Tests and diagnostic callers
use `server.default_table.match` (and the corresponding table methods) explicitly.

The server's auction worker advances due listings once per second independently
of table timeout checks. Shutdown cancels and awaits it before table/connection
cleanup; a persistence failure retains the existing fail-closed behavior.

## A durable operation

1. A handler opens the appropriate concrete transaction context.
2. The economy stages only the affected accounts. Each affected service's
   `stage_state` method copies its own mutable editors, random generator or
   journal maps. Immutable values and unrelated capabilities retain identity.
3. Domain methods apply the operation to these staged objects.
4. The coordinator saves the affected accounts and service journals together.
5. After a successful save, `publish_state` updates the existing service objects;
   the handler can then send a success reply.

An exception before publication discards the staged changes. A storage error
closes admission and the affected connections; that writer must be restarted.
Durable receipts handle a commit whose acknowledgement was lost.

Diagnostic log failures are separate from saved-state failures. A runtime log
open/write/flush/close error disables that service's diagnostics and reports once
through standard Python logging; responses and disconnect/shutdown cleanup
continue. The game log still opens before startup, and a configured path that
cannot be opened still fails startup. Save errors retain the fail-closed behavior
above.

Use `commerce` for account-only edits, `match_transactions` for match settlement,
`claim_transactions` for auction/prize/event claims and `profile_transactions`
for character-profile records. Each coordinator shows its participants and
commit order explicitly. Copy rules belong beside the service fields they protect.
Do not add a service field without deciding how staging and publication treat it.

These contexts are synchronous and must never cross an `await`. Live service
references remain stable, including table capabilities and cross-service reads.
`StateStore` owns disk/SQL persistence and has four operations: load, full save,
save selected accounts, and save selected accounts with named journals.

## Current limits

One process owns one economy. PostgreSQL uses a session writer lock. Packaged
JSON services and offline administration share `writer_lease(path)` for their
whole lifetime, before loading or recovering state until shutdown completes.
JSON also uses a separate short commit lock and checks the previous file
contents before replacing a save. Callers using `Server` or `JsonStateStore`
directly must acquire the lifetime lease themselves; low-level save methods
retain their existing optimistic writer checks.
All writes are synchronous. JSON rewrites the complete save; PostgreSQL limits
account writes but stores each service journal as a complete JSONB document.
Large journals and slow storage can therefore pause all clients on the event loop.

The configured synthetic login pool is capped at 128 accounts. This is a local
protocol implementation, not public account authentication or a distributed
server. The load tool can create additional dormant accounts to measure storage
growth; those accounts are not additional connected players.
