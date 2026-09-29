# Server size and structure review

## Hosted authentication prototype, 2026-09-28

The current tree has **24,460 physical Python lines across 139 runtime files**.
The authentication addition is 734 net runtime lines over the prior checkpoint:
605 in seven `auth` modules, plus trusted legacy transport bindings and CLI wiring.
The HTTP listener owner moved from administration to `network/http.py` so the
gateway reuses its startup/shutdown behavior instead of adding a second owner.
Templates remain 261 lines and native sources remain 770 lines. Tests are counted
separately: 43,773 lines in 221 files. This is new capability, not a size-reduction
claim; the earlier feature breakdown below is its recorded pre-auth checkpoint.

## Optional administration and native rules, 2026-09-28

The current continuation contains **23,726 physical Python lines in 130 files**
under `src/tmserver`, including blank lines, comments and optional modules. This
checkpoint includes audited event administration, native ABI 2, shared PTL records,
receipt history, and the final query, name and startup fixes. Its breakdown is:

| Responsibility | Python lines |
| --- | ---: |
| Economy, shop, auctions, prizes and transaction coordination | 4,823 |
| Match engine, game rules, AI and optional original-code adapters | 4,794 |
| Event rooms, admission, matches, progress and grants | 3,504 |
| Ordinary rooms, tables, spectators and lifecycle | 2,994 |
| Connections, service routing and chat | 1,515 |
| Rankings and rating history | 1,160 |
| Shared admin queries, audited actions and optional web panel | 1,619 |
| JSON/PostgreSQL storage and migrations | 857 |
| Command-line entry points | 866 |
| Profiles and preferences | 828 |
| Transport and command codecs | 484 |
| Cards and catalogue | 265 |
| Resource helpers and package entry points | 17 |

The web templates add 261 lines; the optional native bridge adds 770 C++/header
lines outside that Python total. Test, research, documentation, generated build
and dependency trees are excluded. Evidence is in
`results/current-source-size.json` in the development checkout.

The larger total primarily reflects added administration and original-code
capabilities. Pydantic/Construct adoption and R6 were readability work, not a
large size reduction: match `handle` is now 100 lines, table dispatch is 209,
shop behavior has one stateless owner, and auction saved shapes use strict
library models. The required Python rules remain available for portable serving;
adding native execution does not delete them. No comparable-scope reduction of
thousands of lines has been demonstrated.

The shared Construct PTL table/service layout adds 32 net Python lines while
removing three places that independently wrote the same offsets. Event outcome
receipts use one private Pydantic model, reducing their owner from 127 to 112
lines and explicit conditionals from 20 to 15 without moving progress rules.
The next small
Pydantic candidate is the event grant journal's saved shape. Its entire
snapshot/restore path is only 48 lines, so it cannot deliver a large reduction.
Match settlement has more repeated fields, but most of its restore logic checks
escrow, payout and recovery invariants that a schema library cannot replace.

Keep counting runtime separately from tests and optional tools. Further cuts
should remove an actual responsibility or duplicated behavior. Compacting syntax,
merging protocol dialects or hiding commit boundaries behind a generic framework
would not make this server easier to review.

## Reservation and timeout consolidation, 2026-09-27

Single-account reservation checks now share two private helpers inside
`EconomyService`: validate the live account capability, or release the exact
issued token. Public service-specific methods and Prize's extra points check
remain explicit. This removes 19 lines; exchange conservation and inventory
settlement retain their distinct rules. Forty-four public before/after cases
are identical and all 120 scoped economy tests pass.

Timeout cleanup also reused a weaker copy of peer-close logic. Replacing it with
the existing ownership-aware operation removes four lines and fixes three
reentry failures. All 62 scoped lifecycle checks pass. The package is now
**20,667 physical Python lines in 81 files**, 23 fewer than `728ff6b` and 180
fewer than the original `844b6cf` size review. This is a small consolidation;
there is still no evidence for a safe multi-thousand-line cut at the same scope.

## Remaining packet duplication review, 2026-09-27

The earlier suggestion to share named packet parsing was reviewed against
`d9b9646`. The two parser/builder pairs total only 75 lines. Match parsing accepts
empty bodies, ignores the secondary header byte and permits broader names and
ASCII characters; economy parsing requires a body, zero secondary byte,
restricted names and printable characters. Their rejection behavior differs too.
Small in-memory probes confirm these distinctions. The identical header-format
expression alone does not justify another helper; a common parser would need
multiple policy switches. Keep these implementations and the unrelated BEL
protocol separate. This review makes no source-size reduction claim and does
not rule out future simplification of the larger service dispatchers.

## Catalogue ownership checkpoint, 2026-09-27

Card generation, sale arithmetic and collection points now live in `card_catalog`
instead of the account/shop service. Match setup and admission import that module
directly. The move preserves the algorithms and existing economy imports; shared
errors have one definition. The runtime package is **20,692 physical Python
lines in 81 files**, 11 more than the preceding checkpoint and 155 fewer than
the original `844b6cf` size review. Module boundaries improve readability here;
they do not reduce the amount of behavior implemented. All 63 scoped tests,
five import/resource checks and clean wheel checks pass; see
[validation](../VALIDATION.md).

## Table ownership checkpoint, 2026-09-27

The default table is now an explicit `OrdinaryTable`, just like additional
tables. `Server` owns shared services and no longer inherits table fields or
methods. Route maps, match callbacks, locks, reservation tokens and reset logic
all refer to the actual table instance. Auction expiry now has a server-owned
worker instead of a special branch in the default table's timeout loop.

This improves ownership rather than reducing source size: the runtime package
is **20,681 physical Python lines in 79 files**, nine more than the preceding
checkpoint and 166 fewer than `844b6cf`. No forwarding properties or dynamic
attribute proxy were added. Fifty test files now access the default table
explicitly, including their assignment and fault-injection sites. Two new tests
cover automatic auction expiry, shutdown and failed persistence.

The complete portable suite passes 1,095 tests with 59 optional PostgreSQL
skips and no failures; see [validation](../VALIDATION.md). The next bounded
readability candidate was separating card catalogue/generation from account and
shop handling, now completed above. It relocated roughly 140 lines and removed
a match-to-shop dependency without reducing total size. A follow-up audit found no
large safe block of remaining duplication; similar validators and settlement
branches often encode different domains or commit boundaries.

## Shared inventory and card checkpoint, 2026-09-27

The runtime package at `844b6cf` contained **20,847 physical Python lines in
77 files**. The first consolidation pass reduces this to **20,672 lines in
79 files**, a net removal of **175 lines**. The two added modules replace
repeated behavior: `inventory_edits.py` shares record parsing, owned-copy
matching and immutable edits across six services; `cards.py` defines card fields
once while retaining native-rule and match validation/encoding adapters.

This is a modest reduction, not a resolution of the server's overall size.
Service authorization, transaction timing and protocol dialect differences
remain explicit. Five compatibility tests cover 55 cases against both old and
new code; 542 card comparisons preserve the former constructors and encodings.
There are 381 distinct passing tests across the focused validation runs. See
`../VALIDATION.md` for logs and packaging checks.

Tests are separate from the runtime count: the preceding checkpoint had 33,195
test lines in 153 files, and this pass adds one 186-line compatibility module.
Research, documentation and dependencies are also excluded. Line counts include
blank lines and comments; reducing those would not simplify the implementation.

Table ownership was the next structural target after this pass and is now
addressed above. The large match/economy dispatchers remain candidates; no
projected line saving is claimed before measuring changes. The older review below
is retained as a historical checkpoint; its paths and counts predate packaging
and subsequent feature work.

## Earlier 13,747-line review

The size is mostly explained by the breadth of the replacement service, with
real duplication and organizational debt from building it feature by feature.
This is a static source review, not a claim that every implemented branch is
necessary or historically accurate. No runtime source was changed for this review.

## What the 13,747-line count actually includes

The earlier number counted every non-test Python file directly in `server/`:
64 files and 13,747 physical lines. It excludes the 91 test files, separate
protocol research directory, documentation, decoded assets and dependencies.

Following local imports from `run_server.py` reaches 56 files and 13,414 lines.
This includes optional features imported by the server, whether enabled or not.
The remaining eight files are the game launcher and research/diagnostic helpers:
333 lines. Moving those files would clarify packaging but hardly change size.

Across the 56 server files, the mutually exclusive line counts are:

| Kind | Lines |
|---|---:|
| Source other than the categories below, including imports and data | 10,831 |
| Docstrings | 973 |
| Blank lines outside docstrings | 1,311 |
| Comment-only lines outside docstrings | 299 |
| Total | 13,414 |

These are physical lines, not statements or a complexity score. Many functions
put several statements on one line; normal formatting could increase the count
while making the code easier to maintain. Removing useful protocol comments
would reduce the number without simplifying behavior.

## Where the size goes

Each file is assigned once, by its main responsibility; this is an approximate
architectural grouping, not a claim that the boundaries are clean today.

| Area | Files | Physical lines |
|---|---:|---:|
| Inventory, shop, trade, auctions, rewards and ratings | 12 | 4,048 |
| Matches, combat, CPU, boards and departures | 10 | 3,150 |
| Events: membership, matchmaking, borrowed games, rewards and observers | 10 | 2,299 |
| Connections, profiles, chat, rankings and server startup | 13 | 2,159 |
| Rooms, tables and ordinary spectators | 11 | 1,758 |
| Game launcher and research/diagnostic helpers | 8 | 333 |
| Total | 64 | 13,747 |

The six `native_*` modules total 1,118 lines. That includes combat/growth,
computer decisions, board generation, special tiles, departure rules and NPC
configuration. Most of the service therefore sits outside those game rules.

Substantial necessary work includes the original encrypted connection and
profile protocols; account-bound room/table admission; ordered startup and turn
acknowledgements; atomic multi-account inventory changes; stake refunds;
inventory overflow and delayed deck editing; disconnect/restart recovery; and
event/spectator lifecycles. A completed turn is a small portion of this scope.

## Concrete cleanup candidates

1. **Share the pure inventory-edit logic.** Shop, trade, auction collection,
   Prize Center, event rewards and match rewards each parse and apply much of
   the same Erase/Decks vocabulary: six/seven-number tuples, batch limits,
   duplicate-card matching, unique deck slots and hexadecimal deck names.
   Relevant methods total 333 lines, but some of those lines are necessary
   service-specific context and commit handling. Extract the common parser and
   immutable edit operation; retain each service's authorization, pending state
   and commit boundary. The different Get/Continue/late-edit semantics matter.

   Locations: economy_service.py:1042, trade_service.py:355,
   auction_service.py:350, prize_service.py:208, event_cards.py:189,
   result_rewards.py:156.

2. **Separate shared representations from service modules.** Card records with
   the same eight fields exist in match_service.py, economy_service.py and
   native_rules.py. Their serializers and validation domains differ, and call
   sites convert by copying each attribute. A shared value representation with
   explicit wire encoders and context-specific validators would reduce that
   glue. Preserve the broader native arithmetic model range; do not impose the
   hosted 250-card catalogue limit on every low-level fixture.

   Named packet parsing/building also lives in both match_service.py and
   economy_service.py. Header-only controls and strict economy bodies differ;
   share the envelope mechanics with explicit parsing policies. The generic
   BEL-separated protocol in tm_protocol.py is a different wire format and
   should remain distinct.

3. **Reduce the largest classes' responsibilities.** match_service.py is 1,541
   lines and economy_service.py is 1,143: together about 20% of the reachable
   server source. The match class contains wire dispatch, lobby configuration,
   combat progression and result/rematch handling. The economy class combines
   catalogue generation, account storage/profile serialization, reservations,
   transaction validation and shop commands. Extract cohesive components rather
   than introduce a general-purpose plugin framework. This improves structure;
   it does not inherently reduce total lines.

4. **Make ownership and adapters explicit.** Server inherits OrdinaryTable,
   while additional tables forward selected attributes to the root through
   `__getattr__`. Replace this asymmetry with explicit table instances and shared
   account/service dependencies. Ordinary and event spectator delivery also
   contain similar queue, writer and cleanup scaffolding, although their locks
   and epochs differ. A small shared delivery component is worth considering.

5. **Separate runtime assets and tools from research artifacts.** Runtime
   catalogue loading currently reaches into `protocol_map/economy_catalog.json`.
   Give runtime data a clear home and keep diagnostic/game-launch tools outside
   the service package. This is packaging clarity, not a major line saving.

## Assessment

The source does not contain an obvious multi-thousand-line disposable framework.
A scan for identical function bodies of at least five physical lines found no
cross-function matches; that narrow scan does not detect the semantic repetition
described above. Direct inspection identified repeated concepts and mixed
responsibilities, not wholesale duplicate feature implementations.

The justified first target is a few hundred lines of shared parsing/editing and
conversion scaffolding, then clearer ownership and smaller components. A large
percentage reduction is unproven until an actual refactor is measured. Existing
encrypted integration and original-code checks should protect transaction and
timing behavior during that work. Cutting features would shrink the server much
more, but would also reduce the requested emulation scope.
