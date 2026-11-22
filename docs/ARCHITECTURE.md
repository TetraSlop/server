# Architecture and code ownership

The runtime is organized by feature under `src/tmserver/`. Start with the
owning package rather than a generic service layer.

| Package | Responsibility |
| --- | --- |
| `cli` | Server, local-play and offline-admin entry points. |
| `admin` | Shared read projections and the optional authenticated operator panel. |
| `accounts` | Self-registered usernames, password hashes and login limits. |
| `network` | Server ownership, connections, dispatch, chat and diagnostics. |
| `protocol` | Encryption, command envelopes, identities and table controls. |
| `cards` | Card values, catalogue and inventory edits. |
| `game` | Match phases, computer opponents and the client's battle, growth and AI rules. |
| `rooms` | Ordinary room configuration, tables, admission and spectators. |
| `events` | Configured event rooms, matchmaking, results and card grants. |
| `economy` | Accounts, shop, trade, auctions, prizes and transaction boundaries. |
| `profiles` | Profile transport, character records, options and saved preferences. |
| `rankings` | Result statistics, ratings and optional weekly projections. |
| `storage` | The PostgreSQL store, save validation and migrations. |
| `data` | Packaged protocol tables and the card catalogue, read through `data/resources.py`. |

Tests mirror these packages under `tests/`, with shared fixtures in
`tests/fixtures/`. `examples/` holds optional room, shop and event configurations;
`research/` holds the frozen results of the client's rules; `tools/` holds the check, packaging and
load-test commands. `results/` and `var/` are ignored output and local state.

## Find the code

| Responsibility | Starting points |
| --- | --- |
| Start the server or local client session | [cli/serve.py](../src/tmserver/cli/serve.py), [cli/play.py](../src/tmserver/cli/play.py) |
| Inspect or modify an offline save | [cli/admin.py](../src/tmserver/cli/admin.py), [storage/database.py](../src/tmserver/storage/database.py) |
| Inspect the running server | [admin/queries.py](../src/tmserver/admin/queries.py), [admin/views.py](../src/tmserver/admin/views.py), [admin/server.py](../src/tmserver/admin/server.py) |
| Apply an audited award, event grant or event correction | [admin/actions.py](../src/tmserver/admin/actions.py), [admin/audit.py](../src/tmserver/admin/audit.py) |
| Operator forms for those actions | [admin/awards.py](../src/tmserver/admin/awards.py), [admin/event_grants.py](../src/tmserver/admin/event_grants.py), [admin/event_members.py](../src/tmserver/admin/event_members.py) |
| Shared services and connection lifetime | [network/server.py](../src/tmserver/network/server.py), [network/session.py](../src/tmserver/network/session.py) |
| Route a service request or chat message | [network/dispatch.py](../src/tmserver/network/dispatch.py), [network/chat.py](../src/tmserver/network/chat.py) |
| Decode wire frames, commands or identities | [protocol/](../src/tmserver/protocol/) |
| Card fields, generation and inventory edits | [cards/model.py](../src/tmserver/cards/model.py), [cards/catalog.py](../src/tmserver/cards/catalog.py), [cards/edits.py](../src/tmserver/cards/edits.py) |
| Match state and original game algorithms | [game/match.py](../src/tmserver/game/match.py), [game/rules.py](../src/tmserver/game/rules.py), [game/ai.py](../src/tmserver/game/ai.py), [game/boards.py](../src/tmserver/game/boards.py) |
| Ordinary room rules and table lifecycle | [rooms/config.py](../src/tmserver/rooms/config.py), [rooms/table.py](../src/tmserver/rooms/table.py), [rooms/service.py](../src/tmserver/rooms/service.py) |
| Event configuration and matchmaking | [events/config.py](../src/tmserver/events/config.py), [events/adapter.py](../src/tmserver/events/adapter.py), [events/matchmaking.py](../src/tmserver/events/matchmaking.py) |
| Accounts, purchases, trade and rewards | [economy/service.py](../src/tmserver/economy/service.py), [economy/](../src/tmserver/economy/) |
| LOGIN, registration and password hashes | [accounts/credentials.py](../src/tmserver/accounts/credentials.py), [accounts/provisioning.py](../src/tmserver/accounts/provisioning.py), `Server.login` in [network/server.py](../src/tmserver/network/server.py) |
| Auction records, protocol and escrow | [economy/auctions/](../src/tmserver/economy/auctions/) |
| Profile files and saved player settings | [profiles/service.py](../src/tmserver/profiles/service.py), [profiles/characters.py](../src/tmserver/profiles/characters.py), [profiles/preferences.py](../src/tmserver/profiles/preferences.py) |
| Ratings, statistics and ranking files | [rankings/policy.py](../src/tmserver/rankings/policy.py), [rankings/stats.py](../src/tmserver/rankings/stats.py), [rankings/service.py](../src/tmserver/rankings/service.py) |
| Save, validate or recover durable state | [storage/postgres.py](../src/tmserver/storage/postgres.py), [storage/restore.py](../src/tmserver/storage/restore.py), [storage/validation.py](../src/tmserver/storage/validation.py) |
| Packaged immutable tables | [data/](../src/tmserver/data/), read through [data/resources.py](../src/tmserver/data/resources.py) |

`cli.serve` constructs the two listeners, on loopback unless `--bind` says otherwise. `network.server.Server` owns
shared services; `network.session.Session` owns each connection. The profile
listener is separate in `profiles.service`. Recovered combat, AI, board and
opponent algorithms live in `game`; this server's reward and rating choices live
in their economy and ranking owners.

The optional Starlette Admin listener runs on the same event loop and borrows
that host. Request-local detached projections are captured before rendering;
the panel never opens storage or enrolls accounts. `cli.serve` stops HTTP first,
then profiles and game, before releasing storage. The base installation never imports the web framework.

A loader's LOGIN line arrives on the game listener after the key exchange.
`Server.login` registers an unknown username through `accounts.provisioning`: it
assigns a free POL ID whose raw value, below 2^31, is also the player ID (polcore
sends it as GID and, signed, in the `<CR>` locator), and the account, username and scrypt hash commit together through the
existing store. A known username has its saved hash checked off the event loop. It then mints a game password that lives only
in `account_specs`. The game NICK and the profile digests check that password
exactly as they check a built-in test account's, so profiles need no separate
login. Hashes are saved in the `identities` journal (version 2); a version 1
journal of retired Supabase links restores as empty. The built-in test accounts
are accepted from loopback peers only. [Loader login contract](LOADER_AUTH_SPEC.md).

When explicitly enabled, `admin.actions` shares one synchronous, idempotent
apply per action between web forms and the offline CLI, keyed by request ID.
Strict Pydantic requests and receipts live in `admin.audit`; the change and its
audit receipt publish only after one joint storage commit. Event grants stage
only the event-card and audit journals; account inventory, balance and event
progress remain unchanged until normal collection. Restoration loads event grants before
audit receipts so it can check their original facts even after collection.

`MatchService` calls battle, growth, target and COM placement through
`match.RULES`: `rules.calculate_battle`, `rules.grow_card`, `ai.choose_target` and
`ai.choose_special_move`, ported from the client's routines. Tests can pass a
`rules_backend` with the same four calls. The match owns the random state and
passes its CRT draw function to each call. A calculation failure
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

Import from the feature package, for example `Server` from
`tmserver.network.server` and `MatchService` from `tmserver.game.match`. The
supported entry points are the `tmserver`, `tmserver-db` and `tmserver-admin`
commands and `python -m tmserver`.

## Inside a feature

Use the same boundaries when adding or changing substantial feature code:

| Role | Contents | Examples |
| --- | --- | --- |
| Models | Shared dataclasses, state values, capabilities and field invariants. | [game/models.py](../src/tmserver/game/models.py), [economy/models.py](../src/tmserver/economy/models.py), [events/models.py](../src/tmserver/events/models.py) |
| Packets | Parse requests, validate wire fields and encode replies or native files. | [game/packets.py](../src/tmserver/game/packets.py), [economy/packets.py](../src/tmserver/economy/packets.py), [events/packets.py](../src/tmserver/events/packets.py) |
| Services | Feature behavior, permissions, state transitions and operation-specific staging. | `game/match.py`, `economy/service.py`, `events/service.py` |
| Transactions | Stage the affected accounts/journals; save before publishing or replying. | [transactions.py](../src/tmserver/transactions.py), [staging.py](../src/tmserver/staging.py), `economy/commerce.py` |
| Storage | Implement the save contract with SQLAlchemy Core on PostgreSQL. | [storage/validation.py](../src/tmserver/storage/validation.py), `storage/postgres.py`, `storage/schema.py` |

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

Cross-feature commits go through one `transaction()`. An auction purchase
changes both account balances and the auction journal, so
`transaction(host, keys, "auction")` saves both through one storage call. SQL edits
stay in the shared backend; splitting them into independent feature saves would
break that atomic boundary. Packet encoders can consume model values without
importing the ledger, service or storage backend.

Use this order for a durable request: **decode → stage → change → save → publish
→ reply**. SQL and database row conversion belong in `storage/postgres.py`;
table declarations belong in `storage/schema.py`. Feature transactions call the
storage contract; tests use an in-memory store with the same contract. Domain dataclasses,
native packets and SQL rows have different validation and representation needs;
their conversions stay explicit. No generic CRUD service or inferred packet
serializer is required.

Ranking periods and packets load without the ranking service. Event
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

1. A handler opens `transaction(host, keys, *services)`.
2. The economy stages only the affected accounts. Each named service (and the
   services it depends on) is staged through the `Staged` mixin: the containers
   in its `_staged_fields` are shallow-copied and its `_stage_keys` hook copies
   the mutable editors of the keyed accounts. Other accounts' rows and every
   reservation object keep their identity.
3. Domain methods apply the operation to these staged objects.
4. The context saves the affected accounts and service journals together.
5. After a successful save, `publish_staged` updates the existing service objects;
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

[transactions.py](../src/tmserver/transactions.py) lists every staged service,
its journal and its dependencies. Match settlement stages `MATCH_SERVICES`,
event claims `EVENT_SERVICES`; [economy/commerce.py](../src/tmserver/economy/commerce.py)
wraps the account-only edits. Copy rules belong beside the service fields they
protect: add a field that the service changes to `_staged_fields`, and copy its
per-account mutable rows in `_stage_keys`.

These contexts are synchronous and must never cross an `await`. Live service
references remain stable, including table capabilities and cross-service reads.
Single-account commits share token-type, identity and account-view checks; Prize
Center also checks its independent points. Release checks only token type and
identity so cleanup can discard a reservation whose account view is outdated.
A store has three operations: load, full save, and save selected accounts with
named journals. Supported service-journal names live in `storage.validation`.
`storage.open_store(url)` opens the PostgreSQL writer for the command-line tools.

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
live saves are excluded. Runtime logs belong in the selected working directory's
`var/`; the save is in PostgreSQL.

## Current limits

One process owns one economy. PostgreSQL uses a session writer lock, held from
startup until shutdown completes, and offline administration takes the same
lock. `Server(store=None)` keeps state in memory only and saves nothing.
All writes are synchronous. PostgreSQL limits account writes but stores each
service journal as a complete JSONB document.
Large journals and slow storage can therefore pause all clients on the event loop.

The local test account pool is capped at 128 accounts; other accounts register
themselves with LOGIN. Login rate limits are in memory, per process. The server
is one process, not a distributed service.
