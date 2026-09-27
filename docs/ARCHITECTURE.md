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
