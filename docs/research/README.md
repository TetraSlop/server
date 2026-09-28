# Tetra Master local server experiment

This is an archived experiment overview. Commands, flat module names, scratch
paths and validation counts below describe the recorded stages of development.
For current setup and package paths, use the [project README](../../README.md)
and [architecture guide](../ARCHITECTURE.md). Current operating instructions are
in [local play](../LOCAL_PLAY.md), [database setup](../DATABASE.md) and
[offline administration](../ADMIN.md).

The original PC client completed a full ten-placement practice match and returned
to the room. The user confirmed success; archived traffic is in
`native_runs/20260926_user_cpu_complete/`. The native shop welcome screen also
opens with the expected balance. A native purchase, two-client trade and
post-change relaunch have not been verified.

The current server also passes a replay of37 unchanged application commands
from that capture, through room/table/chat admission and first-turn selection.
This caught and fixed a handle-ID/character-ID mismatch. Fresh graphical
validation is still separate; current test checkpoints are in
`INTEGRATION_STATUS.md`.

Ordinary human rounds now also persist the recovered VS. Player Games counter.
CPU games preserve it. The latest change passed307 focused tests after the
880-test complete checkpoint; continuous original receivers accepted398 packets
from complete two/three-player rounds and rematches. See
`../protocol_map/FULL_MATCH_CONSUMERS.md` for the exact validation scope.

Desktop control is stopped at the user's request. Development continues through
code inspection, original-instruction emulation and encrypted-socket tests.
Browser work remains paused. Scratch edits do not alter installed game files or
existing repositories. The separate loader refactor is
`C:/Users/sruon/Documents/GitHub/tmloader`, with no remote or upstream push.

## Start the existing interactive client

Double-click `../Launch local game.cmd`, or from the parent directory:

    python -B server/run_local_game.py

This starts both loopback services and the existing wrapper until the game closes.
The wrapper has an asInvoker manifest. A staged `tm_startup_probe.next.exe` is
promoted when the current executable is no longer running. Unit tests do not
launch the game or control the desktop.

LocalPlayer starts with ten decoded-catalog cards and 10,000 T. Durable state is
`local_state.json`; pending purchases and unfinished trades roll back.
Ports 51241 and 51220 bind to 127.0.0.1. Logs are `local_game.log`,
`local_traffic.jsonl` and `local_profile_traffic.jsonl`.

An optional PostgreSQL backend now stores accounts, ordered cards and all durable
service journals in one transaction. It uses SQLAlchemy Core and Alembic, keeps
game rules independent of storage, and enforces one server writer. Import/export
preserves existing saves and native profile bytes. See [DATABASE.md](../DATABASE.md)
for setup and [PERSISTENCE_DESIGN.md](../PERSISTENCE_DESIGN.md) for the boundaries.
The combined game launcher uses JSON by default; `--database` explicitly selects
the configured PostgreSQL database.

To run just the server, with no game launch or desktop interaction:

    python -B server/run_server.py

Use `--mode versus` for two human participants, or add `--players 3`
for the25-cell three-human table and third local account.
Use `--players auto` to start with either two or three humans, choosing board
size when the owner presses Start. See `AUTO_TABLES.md` for admission and
configuration details.
Add `--card-prizes` to enable original winner-selected card transfers in human
and CPU matches, and `--wagers` for stakes and double-up. Versus payouts conserve the
players' stake pot; CPU payouts use the recovered offline formula. These are
explicit local hosted policies. Interrupted stakes refund, and unaccepted
earned cards recover through Returned Cards. Default practice stays available.
`--board` accepts sixteen or twenty-five comma-separated original F tile values,
matching the player count. F18 additionally requires `--allow-cbc`; two- and
three-player generated rewards support collection, overflow and recovery.
Other supported choices are `--state`, `--log-dir`, `--game-port`, and
`--profile-port`. This standalone process and the combined game launcher need
separate ports if run concurrently. Ctrl+C closes both services cleanly.
Explicit event rooms, borrowed-card games and card collection can be configured
with `--events server/events.example.json`; see `EVENT_CONFIGURATION.md` and
`EVENT_ADAPTER_NOTES.md`. Three-human event tables need `--accounts 3`.
`--ratings local-history` adopts the recovered offline32-result rating formula
as an explicit hosted policy. Existing rating text is preserved, and ratings
change only when a result commits. See `RATING_POLICY_NOTES.md` for enrollment
and changing modes. `--shop-offers server/shop_offers.example.json` enables
explicit personalized/special packs at their original asset prices.
`--departures` enables recovered practice departure rules, including retained-hand
CPU replacement. Economic/rated departures retain abort/refund behavior.
`--accounts 4 --spectator-preview` permits a fourth account to watch a
three-player table. The fourth synthetic login is EEEEEEEO, with the same local
test password. See `DEPARTURE_ADAPTER_NOTES.md` for leave/reconnect behavior.

## Implemented

- POL challenge/RSA, custom Blowfish, checksums, authenticated encrypted IRC and
  encrypted profile transport; startup, registration and original card saves.
- Room entry, sequenced roster/table updates and replay. PTL/TD carry accepted
  rules, owner and occupancy. A separate D record advertises the trade service.
- Original Computer menu with30 asset-backed opponents and local CPU logic;
  two/three human seats, owned-card selection, turn barriers, captures/battles/
  combos, results, leave, reconfiguration and rematch. Encrypted tests complete
  successive rounds and reload all participating accounts.
- Shop entry, original catalog/card generation, purchase/Get, repeat purchase
  replay, sale/discard/deck editing, profile export and disk reload.
- Authenticated peer trade consent, service entry, paged offers, confirmation,
  atomic two-account exchange and delayed deck edits. Interrupted exchanges
  roll back and disconnect affected peers for profile resynchronization.
  Reservations exclude concurrent shop/match use.
- All12 mapped player options and positive OptAns after persistence; selected
  game/table defaults survive24/Save and a restart without activating current rules.
- Account-specific profile files, handles, characters and status. Full nonce
  authentication binds each profile request to its account and encryption key;
  bounded key retention preserves Viewer context across game reconnects.
- Save writes flush before replacement. A storage failure stops further
  mutations and closes clients without a success acknowledgement. Restart
  loads the last complete save; connection cleanup runs even on disk errors.

Practice preserves ownership and money while saving original card growth and
coherent result counters/streaks. Combat uses the original battle/RNG formulas,
including visible ties. CPU moves use the original hand/placement/target logic
with an explicitly chosen local personality. Growth commits before result
delivery; aborted rounds do not save partial changes. See `MATCH_SERVICE_NOTES.md`,
`NATIVE_AI_NOTES.md` and `SERVER_RESULT_GAPS.md` for evidence and policy.

Application deadlines and clean disconnect/rebind prevent abandoned tables.
Public/private chat and ranking query/file services are also implemented and
covered by original-consumer and encrypted transport tests. Current work and
remaining boundaries are tracked in `EMULATION_COVERAGE.md`; user-only checks
are in `DEFERRED_REVIEW.md`.

Auctions now include exhibits, bid escrow/refunds, expiry, immutable search
files and the original payment/returned-card/won-card/refund collection flow.
Prize Center grants explicitly configured awards once and exchanges points
for the30 recovered catalogue items. Both save jointly with account inventory
before acknowledgements. Character profiles support the71-field PO reply and
authenticated partial edits. Configured event rooms have owner-scoped files,
authenticated membership, time queries and durable F-card grants. Unclaimed
cards remain available after interruption and event expiry.

Menu-selected special boards use the original generator in CPU and two/three
human matches, including original effect calculations and AI decisions.
Explicit F0..18 boards also support CBC rewards. Result discards/deck edits and restart recovery
through Returned Cards are integrated. Spectator preview is opt-in through
`--spectator-preview`; its native user-dialog timing remains unverified.

`--rooms rooms.example.json --accounts 4` adds independent ordinary rooms and
tables. Two two-human matches can run simultaneously with shared durable
accounts. Each room has its own roster, replay, observers and disconnect
handling. See `ORDINARY_ROOMS_NOTES.md` for configuration and current limits.
`--rooms shared_rooms.example.json --accounts 4` puts two independent human
tables in one room, sharing its roster and ordered announcements.

## Code layout

The backend stays in Python. Keep the native wire contract at the edge and game
rules separate from connection handling and durable state:

- `server.py`: shared service setup, persistence, listeners and host lifecycle.
- `irc_session.py`: per-client framing, authentication, channel commands and
  connection cleanup. `server.Session` remains available to existing callers.
- `pol_wire.py` and `profile_service.py`: recovered encryption/framing and the
  separate profile transport.
- `services.py`, `room_routing.py` and `event_routing.py`: dispatch to the
  appropriate game service.
- `ordinary_table.py` and `event_adapter.py`: table lifecycle, reservations,
  delivery and coordination with persistent accounts.
- `match_service.py` and `native_*.py`: the match state machine and recovered
  game rules.
- `economy_service.py` and the auction/trade/reward services: inventory and
  economic operations.
- `commerce.py`: staged shop and trade transactions, with durable commit before
  live inventory publication or protocol replies.
- `match_transactions.py`: the same commit boundary for ordinary-match accounts,
  stakes, ratings, prize transfers and recovery journals.
- `claim_transactions.py`: staged auction, Prize Center and event claims and
  event outcome receipts; `profile_transactions.py` stages character-profile edits.
- `state_store.py`, `postgres_store.py` and `database.py`: storage boundary,
  scoped PostgreSQL writes, and offline migrations/import/export.

Each ordinary table has an explicit `host` reference for shared inventory,
ratings, persistence, connections and delivery. Match state, reservations,
locks, deadlines and observer queues belong to the table. The primary table
still lives on `Server` for compatibility; additional tables follow the same
ownership rules without implicit attribute forwarding. Departure handling acts
on its table, and result settlement saves before any success packet is sent.

Prefer readable functions and explicit ownership over compressed statements or
adding framework layers. Refactor in small steps against the existing protocol,
persistence and disconnect tests; preserve distinctions between recovered rules
and local hosted policies.

## Validation

From the parent directory:

    python -B -m unittest discover -s server -p "test_*.py" -v

The latest complete result is `test_results.txt`; see `INTEGRATION_STATUS.md`.
Tests include actual encrypted loopback traffic, paired trade commit/rollback,
profile saves, injected disk errors and direct original-instruction comparisons.
Scripted/emulator checks are distinct from native UI verification.

The cursor alignment correction was user-confirmed. An additional short-click
buffer is compiled/tested but awaits a user-run native check. Exact pp000..pp999
profile aliases redirect locally, including the pp001 lookup seen after the
completed match. Native post-match reread validation remains pending.

## Remaining work

- Native purchase/sale, trade and profile-reload validation by the user.
- Native checks of wagers, ordinary card prizes, ratings and three-player special boards.
- Event mode8/observers, automatic missions and historical ranked reward policies.
- Historical participant-resume behavior and alternate settlement modes.
- Native parity testing of the standalone loader refactor.

Build tools: `build_probe.cmd`, `build_codec_probe.cmd` and
`build_startup_probe.cmd --pending`. Original binaries/assets are required and
not redistributed. Default accounts: EEEEEEEF and EEEEEEEK, password tm-local-test.
`--accounts 3` or `--mode versus --players 3` enables EEEEEEEA.
`--accounts N` supports2..128 synthetic local identities, with stable IDs and
the same fixture password. `--accounts 8 --list-accounts` prints their login
IDs without starting listeners or writing state. The first four remain
EEEEEEEF, EEEEEEEK, EEEEEEEA and EEEEEEEO. Room admission still has its explicit
local16-human limit; additional rooms share the same account pool.
Both transports retain a bounded connection limit, increasing from8 to the
configured account count when more than8 accounts are enabled.
Protocol atlas: `../protocol_map/PROTOCOL_MAP.md`.
