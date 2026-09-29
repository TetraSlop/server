# Architecture and code ownership

The runtime is organized by feature under `src/tmserver/`. Start with the
owning package rather than a generic service layer. A package move does not
change the wire protocol, saved-state format or service lifetime.

## Find the code

| Responsibility | Starting points |
| --- | --- |
| Start the server or local client session | [cli/serve.py](../src/tmserver/cli/serve.py), [cli/play.py](../src/tmserver/cli/play.py) |
| Inspect or modify an offline save | [cli/admin.py](../src/tmserver/cli/admin.py), [storage/database.py](../src/tmserver/storage/database.py) |
| Inspect the running server | [admin/queries.py](../src/tmserver/admin/queries.py), [admin/views.py](../src/tmserver/admin/views.py), [admin/server.py](../src/tmserver/admin/server.py) |
| Preview and commit an audited operator award | [admin/actions.py](../src/tmserver/admin/actions.py), [admin/audit.py](../src/tmserver/admin/audit.py), [admin/awards.py](../src/tmserver/admin/awards.py) |
| Queue an audited event card or money grant | [admin/event_actions.py](../src/tmserver/admin/event_actions.py), [admin/audit.py](../src/tmserver/admin/audit.py) |
| Correct event eligibility or progress | [admin/member_actions.py](../src/tmserver/admin/member_actions.py), [admin/event_members.py](../src/tmserver/admin/event_members.py) |
| Shared services and connection lifetime | [network/server.py](../src/tmserver/network/server.py), [network/session.py](../src/tmserver/network/session.py) |
| Route a service request or chat message | [network/dispatch.py](../src/tmserver/network/dispatch.py), [network/chat.py](../src/tmserver/network/chat.py) |
| Decode wire frames, commands or identities | [protocol/](../src/tmserver/protocol/) |
| Card fields, generation and inventory edits | [cards/model.py](../src/tmserver/cards/model.py), [cards/catalog.py](../src/tmserver/cards/catalog.py), [cards/edits.py](../src/tmserver/cards/edits.py) |
| Match state and original game algorithms | [game/match.py](../src/tmserver/game/match.py), [game/rules.py](../src/tmserver/game/rules.py), [game/ai.py](../src/tmserver/game/ai.py), [game/boards.py](../src/tmserver/game/boards.py) |
| Ordinary room rules and table lifecycle | [rooms/config.py](../src/tmserver/rooms/config.py), [rooms/table.py](../src/tmserver/rooms/table.py), [rooms/service.py](../src/tmserver/rooms/service.py) |
| Event configuration and matchmaking | [events/config.py](../src/tmserver/events/config.py), [events/adapter.py](../src/tmserver/events/adapter.py), [events/matchmaking.py](../src/tmserver/events/matchmaking.py) |
| Accounts, purchases, trade and rewards | [economy/service.py](../src/tmserver/economy/service.py), [economy/](../src/tmserver/economy/) |
| Durable login ownership and automatic accounts | [accounts/identities.py](../src/tmserver/accounts/identities.py), [accounts/provisioning.py](../src/tmserver/accounts/provisioning.py) |
| Auction records, protocol and escrow | [economy/auctions/](../src/tmserver/economy/auctions/), [economy/claim_transactions.py](../src/tmserver/economy/claim_transactions.py) |
| Profile files and saved player settings | [profiles/service.py](../src/tmserver/profiles/service.py), [profiles/characters.py](../src/tmserver/profiles/characters.py), [profiles/preferences.py](../src/tmserver/profiles/preferences.py) |
| Ratings, statistics and ranking files | [rankings/policy.py](../src/tmserver/rankings/policy.py), [rankings/stats.py](../src/tmserver/rankings/stats.py), [rankings/service.py](../src/tmserver/rankings/service.py) |
| Save, validate or recover durable state | [storage/json.py](../src/tmserver/storage/json.py), [storage/postgres.py](../src/tmserver/storage/postgres.py), [storage/validation.py](../src/tmserver/storage/validation.py), [storage/recovery.py](../src/tmserver/storage/recovery.py) |
| Packaged immutable tables | [data/](../src/tmserver/data/), read through [data/resources.py](../src/tmserver/data/resources.py) |

`cli.serve` constructs the two loopback listeners. `network.server.Server` owns
shared services; `network.session.Session` owns each connection. The profile
listener is separate in `profiles.service`. Recovered combat, AI, board and
opponent algorithms live in `game`; explicit local reward and rating policies
remain in their economy/ranking owners.

The optional Starlette Admin listener runs on the same event loop and borrows
that host. Request-local detached projections are captured before rendering;
the panel never opens storage or enrolls accounts. `cli.serve` stops HTTP first,
then profiles and game, before releasing storage and the required native rules
context. The base installation never imports the web framework.

The optional auth gateway verifies a provider identity before asking
`accounts.provisioning` to allocate a native account. Account data and the
Pydantic ownership journal commit together through the existing store. The
registry restores without web dependencies; both raw listeners reject its owned
accounts unless a trusted play-session access object authorizes the stream.
Legacy pilot links import at startup. No loader-supplied name or identifier
controls ownership, and tickets remain process-local capabilities.

When explicitly enabled, `admin.actions` shares synchronous preview/commit logic
between web forms and the offline CLI. Strict Pydantic requests and receipts live
in `admin.audit`; award and audit publish only after one joint storage commit.
`admin.event_actions` applies the same contract to event grants. It stages only
the event-card and audit journals; account inventory, balance and event progress
remain unchanged until normal collection. Restoration loads event grants before
audit receipts so it can check their original facts even after collection.

`game.backends.RulesBackend` describes the borrowed calculation dependency.
It requires battle, growth, target choice and ordinary/special/three-player COM
decisions. The CLI validates and opens native ABI 3 before any save or listener,
then closes it after the services drain. Direct API users can supply an owned
`NativeRules` context; the default loads the environment-configured process
context on first calculation. There is no Python algorithm backend or capability
fallback. Python retains match state and random state. A calculation failure
aborts the affected round without retrying already consumed randomness.
Final card snapshots publish together after every player's growth succeeds.
A late failure therefore cannot expose a partially completed result.

These packages use direct imports and explicit object references. Their
initializers do not aggregate or construct services. Shared packet helpers live
in `game.packets` and `economy.packets`, whose accepted dialects differ;
`protocol.commands` owns the separate BEL-delimited command format. Profiles,
chat, rooms and events import codecs directly from their owners. Those codecs
can be used without loading the match or shop service. `events.packets` owns
event file encodings, `rooms.packets` directory/roster records,
`rankings.packets` ranking rows, and `profiles.packets` profile response framing.
The room and zone directory layouts are Construct schemas in `rooms.records`,
shared by default, ordinary and event room builders. Field widths, constants
and padding are defined there once; callers supply the room's policy values.

The old flat Python module names have moved, with no compatibility namespace.
For example, import `Server` from `tmserver.network.server` and `MatchService`
from `tmserver.game.match`. CLI names and `python -m tmserver` are unchanged;
use the [README](../README.md) for installation and invocation.

## Inside a feature

Use the same boundaries when adding or changing substantial feature code:

| Role | Contents | Examples |
| --- | --- | --- |
| Models | Shared dataclasses, state values, capabilities and field invariants. | [game/models.py](../src/tmserver/game/models.py), [economy/models.py](../src/tmserver/economy/models.py), [events/models.py](../src/tmserver/events/models.py) |
| Packets | Parse requests, validate wire fields and encode replies or native files. | [game/packets.py](../src/tmserver/game/packets.py), [economy/packets.py](../src/tmserver/economy/packets.py), [events/packets.py](../src/tmserver/events/packets.py) |
| Services | Feature behavior, permissions, state transitions and operation-specific staging. | `game/match.py`, `economy/service.py`, `events/service.py` |
| Transactions | Coordinate the affected accounts/journals; save before publishing or replying. | `economy/commerce.py`, `economy/claim_transactions.py`, `economy/match_transactions.py`, `profiles/transactions.py` |
| Storage | Implement the shared save contract using JSON or SQLAlchemy Core. | [storage/base.py](../src/tmserver/storage/base.py), `storage/json.py`, `storage/postgres.py`, `storage/schema.py` |

Import a shared value or codec from its owner, not through a service that happens
to use it. Models and codecs must be usable without constructing a service,
opening storage or importing an optional database dependency. Model constructors
can retain pure validation and value conversions: for example, a match card's
numeric parsing and an event configuration's fingerprint. Event configuration
uses local codec imports where needed to avoid an eager import cycle.

Keep a private operation-specific record next to its operation; a dataclass
alone does not require another module. The existing focused algorithm, policy,
configuration and ledger files remain useful units. This convention separates
shared concerns in the large services without adding empty layers to every
small feature.

When a feature needs several substantial modules, give it a package with the
same internal names. For example, `economy/auctions/` contains `models.py` for
shared records, `packets.py` for native files and replies, `service.py` for
requests and connection-local state, and `ledger.py` for escrow and settlement.
Tests mirror that package under `tests/economy/auctions/`. A small cohesive
feature can stay in a single file until that separation helps readers.

Cross-feature commits stay in the transaction coordinator. An auction purchase
changes both account balances and the auction journal, so
`economy/claim_transactions.py` saves both through one storage call. SQL edits
stay in the shared backend; splitting them into independent feature saves would
break that atomic boundary. Packet encoders can consume model values without
importing the ledger, service or storage backend.

Use this order for a durable request: **decode → stage → change → save → publish
→ reply**. SQL and database row conversion belong in `storage/postgres.py`;
table declarations belong in `storage/schema.py`. Feature transactions call the
storage contract, and JSON implements the same contract. Domain dataclasses,
native packets and SQL rows have different validation and representation needs;
their conversions stay explicit. No generic CRUD service or inferred packet
serializer is required.

Ranking periods import `RankingHistory` from `rankings.models`. Event
configuration imports `EventTableConfig` from `events.models`, without loading
matchmaking or the match service. Profile startup imports room directory bytes
from `rooms.packets`; it does not construct a room service. Its initial directory
files come directly from that encoder, with no discarded duplicate records.
Private coordinator entries and rounds remain beside their state machines.

## Protocol and shared values

The connection router handles service requests before peer delivery. Native game
messages sent between players must be normalized chat or validated direct trade
controls. Original control receivers can adopt an arbitrary sender, and their
lower receive path ignores the fifth service-marker byte; generic `GTM0` relays
would therefore let a player impersonate service controls. Server-owned replies
use their normal service delivery paths. Opaque non-game relay traffic is retained.

Runtime state uses Python dataclasses. Room and event configuration uses strict
Pydantic validation, retaining frozen dataclass constructors and `replace` where
those interfaces are already used. JSON conversion stays at the configuration
boundary, including text encoding, route IDs and automatic seating. Unknown
fields and numeric coercions are rejected; cross-field game rules and route
collisions remain explicit domain checks. Auction persistence now uses the same
strict boundary for saved record shapes in `economy/auctions/persistence.py`;
the ledger retains identity, escrow, catalogue and cross-record invariants.
Its version-1 JSON representation and legacy seven-field cards remain supported.
SQLAlchemy Core and Alembic remain optional persistence dependencies.

`cards.model.Card` defines the eight stored card fields once. Native rules and matches
keep thin adapters for their different validation domains and wire encodings;
`Card.from_card` converts through the receiving constructor. Match-specific card
validation lives in `game.models.Card`; economy callers import the shared card
value directly from `cards.model`.

`cards.catalog` owns decoded card data, card generation, prices and collection
points. Matches and admission rules import it directly without depending on
account/shop handling. `economy.errors` defines the shared rejection type;
callers import the error and catalogue API from their respective owners.
Catalogue construction loads packaged data or an explicit JSON path; matches
also accept an injected catalogue. Neither operation creates accounts, loads
saves or runs a service.

`cards.edits` parses numbered card records, matches owned copies in stable
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

Game and profile handlers release their task registrations even if transport
cleanup is cancelled or raises. Profile handlers also release their writer
registrations, which bound admission. The cleanup guard does not suppress
cancellation or change which close errors propagate.

Connection reads recheck closure before processing buffered input. Timeouts,
worker failures and table departure share a peer cleanup operation that rechecks
the current owner before closing. An earlier close can release the old table and
wait on delivery while a survivor reenters.

Event cancellation, deferred observer cleanup and failed-driver cleanup also
recheck ownership immediately before closing a released connection. A new table,
event admission, observer admission or busy account keeps that connection alive.
Peers still owned by a failed event driver close; shared storage failure also
requires closure even after admission to another service.

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
continue. The game log opens after configuration and saved-state validation,
before recovery can write. A configured path that cannot be opened fails before
recovery; a later recovery failure closes the log even while its exception is
retained. Save errors retain the fail-closed behavior above.

Use [economy/commerce.py](../src/tmserver/economy/commerce.py) for account-only
edits, [economy/match_transactions.py](../src/tmserver/economy/match_transactions.py)
for match settlement, [economy/claim_transactions.py](../src/tmserver/economy/claim_transactions.py)
for auction/prize/event claims and [profiles/transactions.py](../src/tmserver/profiles/transactions.py)
for character-profile records. Each coordinator shows its participants and
commit order explicitly. Copy rules belong beside the service fields they protect.
Do not add a service field without deciding how staging and publication treat it.

These contexts are synchronous and must never cross an `await`. Live service
references remain stable, including table capabilities and cross-service reads.
Single-account commits share token-type, identity and account-view checks; Prize
Center also checks its independent points. Release checks only token type and
identity so cleanup can discard a reservation whose account view is outdated.
`StateStore` in `storage.base` defines the shared persistence boundary with four operations: load, full save,
save selected accounts, and save selected accounts with named journals.
Supported service-journal names also live in `storage.base`; PostgreSQL, schema
declarations and snapshot validation do not import the JSON implementation.

## Tests and packaged resources

[tests/](../tests/) mirrors the feature directories, with protocol fixtures in
`tests/fixtures/`. Domain and encrypted integration tests stay beside the feature
they exercise; moving a test does not change its validation scope. Some tests
reuse clients and setup helpers from other feature tests through explicit imports.
Import a helper's module rather than aliasing its `TestCase` class at module
scope, which can cause unittest to discover that class a second time.

Run discovery from the repository root:

```powershell
python -m tools.check_collection
python -m unittest discover -s tests -t .
```

A feature can be checked with `python -m unittest discover -s tests/game -t .`.
Existing named-test invocations must use the new feature-qualified module path.
The [validation record](VALIDATION.md) records executed checks separately from
this layout guide.

Immutable JSON/TSV resources stay in `tmserver/data/`; callers use
`data.resources` rather than deriving paths from their source file.
`storage/database.py` and `storage/migrations/` stay together so offline
migration lookup works in an installed wheel. Package data includes these
resources and migration scripts. Test fixtures, research binaries, captures and
live saves are excluded. Runtime saves and logs belong in the selected working
directory's `var/`, as described in [local play](LOCAL_PLAY.md).

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
