# Server size and structure review

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
