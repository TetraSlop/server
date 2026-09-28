# Packaging and transaction validation

## Room, ranking and profile boundaries, 2026-09-27

Shared event-table configuration and deliveries now live in `events.models`;
ranking history lives in `rankings.models`. Room directories/roster records,
ranking rows and profile framing have dedicated packet modules. Consumers use
those owners directly: event configuration no longer loads matchmaking, weekly
periods no longer load the ranking service, and profile startup no longer loads
the room service. Private coordinator and ranking-entry records stay local.

Profile startup previously built ZL/RL records, then unconditionally replaced
both with `directory_files()`. It now uses that encoder directly. An isolated
comparison with a coherent `dd92a6b` archive preserves all **296 exposed
directory/account/status records (2,047,596 bytes)** across default and
2/3/8/128-account configurations. All **2,611 profile codec cases** agree on
return values or exception type/message; all **522 room codec cases** also
match. Evidence: `results/profile-bootstrap-equivalence.json` and
`results/room-packets-equivalence.json`.

Independent review confirms all 24 moved definitions and their dependencies,
87 migrated consumer imports, and retained non-import behavior across 54
runtime/test/probe files. The only behavior-body edit is removal of those dead
profile initialization statements. There are no eager cycles across 105 runtime
modules or stale moved-symbol references across 294 active Python files.
Fresh-process imports verify configuration/period/codec isolation, including
combined and reverse import order. Evidence:
`results/small-boundary-independent-{ast,import}-review.json`.

The final scoped run passes **136 tests in 96.913 seconds**, covering encrypted
profile operations, rooms, events, rankings and service integration. Log:
`results/small-boundaries-final-tests.log`. Earlier extraction controls also
pass (98 ranking, 34 event, 29 room tests); these overlap the final run and must
not be summed as distinct coverage. Collection remains 1,170 tests with no
import errors. Lint and whitespace checks pass. The preceding full suite was
not repeated for these focused extractions; no live PostgreSQL or native UI
run was made.

The clean 113-entry wheel passes entry points, administration/recovery,
temporary listeners and extended model/codec import isolation checks. All 108
runtime files match source; packaged metadata includes the current README.
SHA256: `4d1302755fc9be9e9f476f466a1dc7f322333f63194983c84e8ed18f79af95c8`.
Evidence: `results/small-boundaries-wheel-result.json` and
`results/small-boundaries-wheel-{build,check}.log`.
Protocol bytes, local policies and save formats are preserved.

## Models, packets and storage boundaries, 2026-09-27

The main game, economy and event features now separate shared models and packet
codecs from their services. Runtime, tests, tools and active research probes
import values from their owning modules. `storage/base.py` owns the shared
StateStore contract and journal names; PostgreSQL, schema declarations and
snapshot validation no longer import the JSON backend. SQL operations and
transaction sequencing are unchanged.

Independent review against `e1448cc` verified 59 moved definitions and the
remaining behavior of all 32 changed existing runtime modules by non-import
AST comparison. Four existing re-exported aliases, 380 retained import bindings
and 113 moved-definition dependency origins resolve to their intended owners.
All 126 changed test modules retain their non-import ASTs; discovery preserves
the exact 1,170 test IDs. Evidence: `results/code-boundary-independent-review.json`
and `results/code-boundaries-test-review.json`.

An isolated before/after comparison exercises **1,859 codec cases with zero
mismatches**: 836 game, 852 economy and 171 event cases. Encoded/file bytes,
parsed values and exception types/messages agree. The corpus includes malformed
headers, oversized bodies, ASCII/control characters, configured event files,
opaque templates, configuration fingerprints and service records. Intentional
match/economy dialect differences remain intact. Evidence:
`results/packet-boundaries-equivalence.json`.

Fresh-process checks import all 98 runtime library/package modules, including
95 without site packages. All six new model/packet modules load without the
match, shop or event services, the server, or SQL dependencies. EventConfig
works with models-first and packets-first import order; its method-local codec
imports retain validation without eager cycles. The eager dependency graph has
no cycles. The installed-wheel check now exercises this model/codec isolation
and representative packet/card round trips as well as normal entry points.

Focused controls passed during extraction: 145 economy, 58 event and 74 storage
tests. After canonicalizing consumers, 150 game/economy/event/preference tests
also passed. These runs overlap and are not a combined distinct-test count.
Logs: `results/economy-model-extraction-tests.log`,
`results/event-boundaries-tests.log` and `results/storage-boundary-tests.log`.

The final complete suite ran **1,170 tests in 935.393 seconds: 1,111 passed,
59 PostgreSQL tests skipped, zero failures or errors**. Runtime/test/tool source
hashes stayed unchanged throughout the run, and every loaded server module came
from this checkout. Evidence: `results/code-boundaries-full.log` and
`results/code-boundaries-full-result.json`. Lint, staged whitespace and current
documentation links also pass.

The clean 109-entry wheel passes the codec/model isolation check, entry points,
JSON administration/recovery and temporary listeners. All 104 runtime files
match source, and packaged project metadata includes the current README.
SHA256: `8fc7cd469dfa329a69a83032be85bbf7c359dc068585708af6452cb56148f77b`.
Evidence: `results/code-boundaries-wheel-result.json` and
`results/code-boundaries-wheel-{build,check}.log`. Optional database imports
were checked, but no live PostgreSQL or native game UI was run.

This establishes the shared-code boundary in the large services. Smaller
cohesive modules still keep operation-specific records near their operations;
ARCHITECTURE.md documents when to extract those. No new runtime dependency,
save schema, gameplay policy or native UI behavior is introduced.

## Feature package layout, 2026-09-27

Moved the 77 flat runtime modules into `cards`, `cli`, `data`, `economy`,
`events`, `game`, `network`, `profiles`, `protocol`, `rankings`, `rooms` and
`storage`. Only `__init__.py` and `__main__.py` remain as root Python modules.
The 156 test modules now use matching feature directories; the local-play peer
helper lives in `tests/support`. Shared test locations preserve fixture paths,
temporary-state locations and subprocess working directories.

Imports, patch targets, diagnostic logger names, console entry points,
`Play.cmd`, active research probes and wheel checks use the new paths. The
editable installation was refreshed. Console command names and
`python -m tmserver` are unchanged; old flat Python module imports have moved
without forwarding shims. Package initializers remain free of service imports.
Database code and its migrations moved together under `storage`.

All 77 moved runtime modules retain their non-import ASTs from `7eb34c7`.
Independent review verified the mapped import origins, aliases and scopes,
including lazy imports. All 157 moved test/helper modules match after only
the recorded import and path transformations. Discovery preserves the exact
multiset of 1,170 baseline test IDs under their new module names. Fixtures,
packaged JSON/TSV data and the frozen initial migration are unchanged.
Evidence: `results/test-layout-{discovery,validation}.json` and
`results/runtime-layout-moves.json`.

The complete portable suite ran **1,170 tests in 904.834 seconds: 1,111 passed,
59 PostgreSQL tests skipped, zero failures or errors**. The run verified unchanged
source hashes and that loaded server modules came from this checkout.
Evidence: `results/package-layout-full.log` and
`results/package-layout-full-result.json`. The earlier 134 path-sensitive
controls also passed; they overlap the full suite and are not an additional
coverage count. Lint and staged whitespace checks pass.

The clean 101-entry wheel passes installation, command/module entry points,
JSON administration/recovery and temporary listeners. All 96 packaged runtime
files match source; no old flat module files remain. Alembic discovers the
unchanged `0001_state` head at the new migration location. Wheel SHA256:
`91ff48042df0af3b83f38a6029274fcefdc172e8a7e85108a7bb84d3715c65d2`.
Evidence: `results/package-layout-wheel-result.json` and
`results/package-layout-wheel-{build,check}.log`.

No native game UI or live PostgreSQL run was performed for this reorganization.
Protocol, gameplay policy and save formats are unchanged. Historical research
records retain their original paths; current navigation is in ARCHITECTURE.md.

## Retired event cleanup ownership, 2026-09-27

Three event cleanup paths could close a connection after a new service took
ownership: queued cancellation notices, scheduled observer closure, and a
failed driver's captured peer list. The adapter now shares a final ownership
check across those paths. Current failed-driver participants still close, idle
released peers still close, and shared storage failure retains forced closure.
The ownership decision has no suspension before `Session.close` marks closed.

Eleven new tests cover nine previously failing reentry cases and five passing
cleanup controls. Queued expiry exercises new ordinary, shop and event selection
admissions behind a blocked notification. Observer cases use actual encrypted
`Session.run` input ready before a deferred closer; abort and failed-send paths
preserve shop/table/watch reentry. Driver cases pause the first socket's close
while a different selection exits and enters an ordinary table or shop.
The driver baseline used a coherent archived `27448f7` runtime and verified its
loaded adapter hash. Evidence: `results/event-queued-cleanup-{before,after}.log`,
`results/event-deferred-cleanup-{before,after}.log` and
`results/event-driver-reentry-{before,controls}.log`.

The combined event expiry/gameplay/observer/chat/room/session run passes all
**64 tests in 95.924 seconds**. Together with the separate driver isolation run,
**66 distinct focused tests pass**, including encrypted shared-storage failure
and successful ordinary CPU completion after an event-local failure. Commands
and source hashes: `results/event-cleanup-ownership-result.json`; main log:
`results/event-cleanup-ownership-controls.log`.

Independent review found no material issue. Collection finds 1,170 tests with no
import errors; lint and whitespace checks pass. The clean 89-entry wheel passes
installation, entry points, JSON administration/recovery and temporary listeners;
all 84 runtime files match source. Runtime is 20,684 physical Python lines.
SHA256: `367bdd4665e345f25a435c8a4e9743722ba026d1556cb4fde31cf2474d773aac`.
Package evidence: `results/event-cleanup-ownership-package-result.json` and
`results/event-cleanup-ownership-wheel-{build,check}.log`.
No full-suite, PostgreSQL or native UI run is claimed, and no save format or
historical gameplay policy changed.

## Shared reservation checks and timeout cleanup, 2026-09-27

The timeout path's nickname-only close loop could disconnect a survivor after
entry into a replacement match, another table or shop while earlier room
notifications waited. The new injected-clock regression fails in all three
cases on `728ff6b`; its unclaimed-peer control already passed. Reusing
`close_released_peers` fixes those failures. All **62 lifecycle/watchdog tests
pass in 43.254 seconds**, covering cleanup, deadlines, departures and session
recovery. Evidence: `results/timeout-reentry-{before,after}.log` and
`results/timeout-consolidation-controls.log`.

Two private economy helpers consolidate single-account token validation and
release. Public APIs, exact error messages/order, token identity, release
idempotence and Prize's independent points check are preserved. All **44 public
before/after comparisons match byte-for-byte** and **120 scoped tests pass in
0.322 seconds**, including rollback, forged tokens, delivery and reward claims.
Exact commands: `results/economy-capability-validation.json`; comparison probe:
`results/economy-capability-equivalence.py`. Independent review found no semantic
difference. Runtime decreases by 23 lines to 20,667; no save format changed.

Collection finds 1,159 tests with no import errors; lint and whitespace checks
pass. The 89-entry wheel passes clean installation, entry points, JSON
administration/recovery and temporary listeners. All 84 runtime files match
source; SHA256 is
`b729e3ae40830e81bf5f15ff0e689e7e669a95ded25ba2f3fb13ef1f1f0439ca`.
Evidence: `results/reservation-timeout-result.json` and
`results/reservation-timeout-wheel-{build,check}.log`. The 182 focused tests are
distinct; no new full-suite, PostgreSQL or native UI run is claimed.

A separate read-only configuration audit found no concrete defect: 29 synthetic
route/capacity cases, 110 complete event-round configurations and 23 existing
ordinary/event controls pass. Evidence and scope are recorded in
`results/room-configuration-audit-summary.json`. These checks cover generated
records and headless game flow, not native UI acceptance or historical policies.

## Closed sessions and retired worker cleanup, 2026-09-27

Two buffered-input regressions fail on the preceding runtime: a valid encrypted
shop request can reopen an account after disconnect cleanup, and a valid login
can bind a closed connection. The read loop now checks closure again after its
awaited read, before authentication or dispatch. Tests use an actual suspended
`StreamReader` and encrypted frames, replacing only transport output.
Baseline evidence: `results/session-buffered-frame-before.log`.

Continuation and departure worker failures could also close a surviving peer
after that peer entered another service while old room notifications waited.
Both workers now use the existing ownership-aware close operation. Two new
tests reproduce six failing reentry cases: same table/new match, another table
and shop for each worker. Two no-reentry controls preserve the required abort.
The tests pause transport drain while real room and admission code runs; they
check replacement reservation identity and unchanged durable state after entry.
Evidence: `results/worker-failure-reentry-regressions-{before,after}.log`.

All **55 focused tests pass in 55.225 seconds**, including encrypted departures,
reconnects, deadline handling, storage failures and event fault isolation:
`results/session-worker-lifecycle-controls.log`. Independent diff review found
no material issue. A separate 16-case spectator/commerce lifecycle probe also
passes without requiring changes; its synthetic-transport scope and source
hashes are in `results/spectator-commerce-audit.json`.

Collection finds 1,158 tests with no import errors. Ruff and whitespace checks
pass. The rebuilt wheel passes clean installation, entry points, JSON
administration/recovery and temporary service listeners; all 84 runtime files
byte-match source. Logs and source hashes: `results/session-worker-lifecycle-*`.
Wheel SHA256: `048e270bee7275fa38a3a5e19ffd9e32243085833b79746485ae4ead7ce1f52e`.
Runtime is 20,690 physical Python lines. No save format or gameplay policy
changed; this was not a full-suite, PostgreSQL or native UI run.

## Post-consolidation behavior audit, 2026-09-27

Read-only review on `d9b9646` found no defect in saved table preferences during
reserved operations. All 38 isolated synthetic checks pass, covering reservation
identity, pending shop purchases, later trade/event/claim settlement, malformed
saves and persistence failure cleanup. Event/trade/shop checks use the account
API; they do not claim those wire routes accept table-setting commands. Evidence:
`results/preferences_reservation_audit.py` and
`results/preferences-reservation-audit.json`.

An apparent ordinary-room departure discrepancy was rejected as a defect:
the captured native sequence sends room `PART`, joins its table, then sends room
`PD` updates. Retaining room presence is required; `PART` is not equivalent to
explicit `PC` departure. Three existing encrypted replay, startup and late-exit
controls pass in 3.199 seconds. Evidence:
`results/room-part-presence-conclusion.json` and
`results/room-part-native-boundary-controls.log`.

No runtime or tests changed. These bounded checks do not replace the preceding
full-suite checkpoint or establish native UI acceptance.

## Card catalogue ownership, 2026-09-27

Card catalogue loading/generation, sale arithmetic and collection points now
live in `card_catalog`, independent of account and shop handling. `EconomyError`
has one shared definition in `economy_errors`; existing economy imports remain
exact aliases. Production catalogue consumers import the owning module directly.

`Catalog`, `collection_points`, `_ARROW_COUNTS` and `EconomyError` are
AST-identical to baseline `035bc8b`, as is the remaining `EconomyService` class.
Five fresh-process checks pass for current-source imports, packaged/explicit-path
resource loading, match construction without importing the account service,
false-valued injected catalogue identity and compatibility exports. Evidence:
`results/card-catalog-extraction-audit.py` and `.log`.

All **63 focused tests pass**: 61 catalogue, economy, shop-offer, opponent-batch,
match, CPU and admission cases in 0.468 seconds, plus two encrypted CPU
prize/rematch/reconfiguration cases in 5.178 seconds. The first selection
includes 2,186 frozen original-client generation/selection/sale vectors. Logs:
`results/card-catalog-focused-tests.log` and
`results/catalog-extraction-rounds.log`.

Collection remains 1,154 tests with no import errors. Ruff and whitespace checks
pass. The rebuilt 89-entry wheel passes clean dependency-free installation,
entry points, JSON administration/recovery and temporary listeners. All 84
runtime files byte-match source. Evidence:
`results/catalog-extraction-wheel-{build,check}.log` and
`results/catalog-extraction-wheel-result.json`.
SHA256: `92f220e42ba1648aa8d796055b0f2c67431f1d0031b975192c08ff4ab1b10bdd`.

Runtime Python is 20,692 physical lines in 81 files, an 11-line increase from
module/import boundaries. This removes a dependency and a mixed responsibility,
not a large quantity of code. The complete suite result below precedes this
extraction; it was not repeated. No PostgreSQL instance or native client ran.

## Explicit ordinary-table ownership, 2026-09-27

`Server` no longer inherits `OrdinaryTable`. Its `default_table` is an explicit
instance, like every additional table, and all routes and callbacks retain the
actual table owner. Auction expiry has its own server task instead of running
only through the default table's watchdog. Protocol packets, save formats and
economic policy are unchanged. Runtime size is 20,681 physical Python lines,
nine more than the preceding checkpoint; this is an ownership improvement.

Fifty existing test files now use explicit default-table access, including
assignments and fault-injection targets. The focused selection passes all
**30 tests in 50.059 seconds**: simultaneous CPU/human tables, failure/timeout
isolation, observer ownership, disconnect/reentry races and match transactions.
Evidence: `results/default-table-ownership-focused.log`. Two new maintenance
tests pass in 0.335 seconds, exercising real listing/bid transactions with two
ordinary tables, durable automatic expiry, shutdown cancellation and failed-save
rollback without retries. Their fixtures replace only the maintenance clock;
table watchdogs retain their own timing. Independent source review and in-memory
table/reset isolation checks found no material issue.

The complete portable suite on the final runtime/test tree passes **1,154
cases in 862.518 seconds: 1,095 passed, 59 optional PostgreSQL skips, no failures
or errors**. Database environment settings were removed before collection.
All 240 source/test/tool/configuration hashes remained unchanged, and all 76
loaded runtime modules resolved inside this checkout's `src` directory.
This full run covers the earlier inventory/card consolidation as well.
Evidence: `results/table-ownership-full.log`,
`results/table-ownership-full-result.json` and
`results/table-ownership-full-manifest.json`.

Ruff and whitespace checks pass. The rebuilt wheel passes a clean dependency-free
install, entry points, JSON administration/recovery and temporary service
listeners. All 82 packaged runtime files byte-match source, in an 87-entry wheel.
Evidence: `results/table-ownership-wheel-{build,check}.log` and
`results/table-ownership-wheel-result.json`.
SHA256: `0eb5374ab1b53448bb4992fe5ab2a189421283832e9f91fbc5223107331410e1`.

PostgreSQL was not started for this checkpoint. Native applications, desktop
input and live saves were not used; local-play tests mock client launches.

## Shared inventory edits and card values, 2026-09-27

Six inventory editors now share pure record parsing and card application, and
the eight stored card fields have one definition. Services retain their existing
authorization, parser dialects, offered-card exclusions and commit boundaries.
The runtime count falls from 20,847 to 20,672 physical Python lines. This is a
consolidation pass with no intended protocol or economic-policy changes.

All **381 distinct tests pass** across the focused runs below (the runs overlap):

| Evidence log under `results/` | Cases | Seconds |
|---|---:|---:|
| `inventory-consolidation-domain.log` | 126 | 0.877 |
| `inventory-consolidation-transactions.log` | 69 | 68.043 |
| `inventory-consolidation-final-values.log` | 67 | 0.836 |
| `card-value-integration.log` | 255 | 322.682 |
| `inventory-edit-compatibility-refactor.log` | 5 | 0.094 |

These cover inventory edits, commerce/claims, trade and match settlement,
restart recovery, native rules/AI, capture replay, and encrypted CPU, event and
two-/three-player rounds. The five added tests exercise 55 compatibility cases
and also pass against archived baseline `844b6cf`:
`results/inventory-edit-compatibility-baseline.log`. They preserve numeric
aliases, authenticated sides, name restrictions, stable duplicate-card selection
and global deck-slot clearing around trade offers.

An independent comparison of the former and current card classes passes all
**542 cases**, including validation exceptions, ranges, encodings, conversion,
equality, immutability and replacement:
`results/card-value-contract-result.json`. Read-only review found no material
runtime issue; 18 additional invalid-edit probes rejected without partial state
changes, and four existing delayed result/trade edit controls passed.

Current collection finds **1,152 tests** without import errors. Ruff and
whitespace checks pass. The rebuilt wheel passes clean dependency-free
installation, entry points, JSON administration/recovery and temporary service
listeners. All **82 packaged runtime files** byte-match source (87 total wheel
entries). Evidence: `results/inventory-consolidation-wheel-{build,check}.log`
and `results/inventory-consolidation-result.json`.
Wheel SHA256: `e80e283c9806d38e33e52cd6d98987c76f26eb8da2ee16e5da0041c2f173f223`.

The whole suite and PostgreSQL suite were not repeated for this refactor; their
older checkpoint results below are not current-source results. Native rendering
and UI acceptance were not exercised.

## Event admission ownership and repeated validation, 2026-09-27

Commit `a42f147` closes the reverse service-boundary gap: an ordinary observer
retaining event-room presence could also enter an event watch or event card
selection. Both paths reproduced before the fix. `EventAdapter` now reuses the
existing room ownership check before Req/EInit admission; ExitCh, PC and PART
handling remain unchanged. No new historical protocol or economic policy is
inferred.

All **42 focused tests pass**: the new regression (both watch and selection)
in 6.695 seconds, 11 leave/reentry controls in 35.566 seconds, and 30 broader
event dispatcher controls in 102.169 seconds. They cover rejection before
transport cleanup can conceal partial mutation, unchanged accounts/rounds and
reservations, explicit exit followed by successful same-socket admission, and
identical valid retries. The broader selection exercises full two/three-player
events, multiple tables, spectators, mode8 and admission expiry. Independent
source review found no additional issue.

Logs: `results/event-service-boundaries-{before,focused,controls,dispatcher-controls}.log`
and the original encrypted reproduction in
`results/event-reverse-observer-admission-repro.log`.

The complete portable suite passes against an isolated archive of baseline
`2a6bf1cc25e4ad319f024b90bb16dfeb8dec7894`: **1,146 tests in 876.507 seconds,
1,087 passed and 59 optional PostgreSQL skips, with no failures or errors**.
The run removed database environment settings and verified all 74 imported
server modules resolved within its archive. The manifest records 255
source/test/tool hashes and archive SHA256
`29b3095b8276a5535ad7c41686fdab5cb234b238c5dcf0c289122d223e30cbc4`.
Its temporary tree was removed and verified absent. Evidence:
`results/full-suite-2a6bf1c.log`, `results/full-suite-2a6bf1c-result.json` and
`results/full-suite-2a6bf1c-manifest.json`.

All **59 PostgreSQL tests pass in 55.163 seconds**, without skips, against a
separate archive of baseline `2a6bf1c`. Its 467 file hashes remained unchanged.
The private PostgreSQL 16.15 instance and its owned resources were verified
removed afterward. See [database evidence](research/ADMIN_POSTGRES_VALIDATION.md).
This baseline precedes `a42f147`; the 42 scoped tests above cover that later
dispatcher-only correction. The full 1,147-case suite was not repeated after
that correction. Native rendering is not part of either check.

A supplemental administration audit passes five scenarios with simultaneous
auction bids/refunds, unfinished stakes, unaccepted result cards, event membership
and old weekly ranking state. Reads preserve exact save bytes; each reward or
event update changes only its owning journal, and exact retries preserve bytes.
No runtime change was needed. Evidence: `results/admin-journal-boundary-audit.json`.

Current collection finds **1,147 tests** without import errors. Repository-wide
Ruff and whitespace checks pass. The rebuilt 85-entry wheel passes a clean
dependency-free install, entry points, JSON administration/recovery and temporary
service listeners. All 80 packaged runtime files byte-match source. Logs:
`results/event-admission-wheel-{build,check}.log`.
SHA256: `6937ece2babc13f40cc5d82527197421e2b8c2f7d2f93ba97f6d3bb859ffe4e5`.

## Room information and observer service boundaries, 2026-09-27

Commit `df49fc7` reserves the terminator in the native 32-byte room information
field: incoming DE/PD accepts at most 31 non-NUL bytes, and generated CPU
information is padded to 31 bytes. The existing status/substatus offsets,
other information bytes and full canonical-name authentication stay intact.
Two new regressions fail before the correction, covering DE/PD mutation/replay
atomicity and single/paired CPU output: `results/room-info-boundary-before.log`.
All **43 tests pass in 13.782 seconds** in this final selection:

```powershell
.venv/Scripts/python.exe -B -m unittest tests.test_room_service tests.test_event_rooms tests.test_services_integration tests.test_room_presence_names tests.test_three_player_room tests.test_shared_room.SharedRoomUnits tests.test_computer_room_adapter -v
```

Log: `results/room-info-boundary-after.log`. Three older shared presence fixtures
now use the original safe length instead of 32 nonzero bytes.

Research `f268eff` executes **41 original/production cases**, establishing both
the original receiver's -8404 diagnostic and a canary-backed sender overread.
The discovery run flags three unsafe production publications. Running
`research/probe_room_info_boundary.py` with `--require-safe-production` against
the correction passes all 41 cases with zero unsafe publications. Transcript:
`results/room-info-safe-production-original.log`; detailed output:
`results/room-info-safe-production-original.json`. See
[ROOM_INFO_BOUNDARY.md](research/ROOM_INFO_BOUNDARY.md) for exact entry points,
image hash and reproduction. The inspected player projection preserves its
name/status/substatus through both PTL and deltas; no visible crash, native UI
improvement or historical denial code is claimed. Independent review found no
material issue with the correction.

Commit `4282205` closes two observer lifecycle gaps. Event observers previously
could enter an ordinary table through direct GameEN/Req while retaining their
event admission. Ordinary observer ExitCh also left stale table chat membership.
The four added runtime lines reuse room ownership validation and exact-table
chat cleanup. Baseline evidence: `results/room-cross-service-observer-repro.log`
and `results/room-service-boundaries-before.log`. The new tests inspect rejected
dispatch before socket-error cleanup could conceal partial mutation, then use
encrypted traffic to verify explicit event exit permits ordinary admission.

The final **15-test selection passes in 20.437 seconds**:
`results/room-service-boundaries-joint.log`. It includes the new boundary module,
spectator reentry and table admission rules modules, plus existing explicit
spectator exit, cross-table admission and event-selection room-transition
controls. Participant state, reservations, unrelated room chat, retired senders
and same-connection observer reentry remain covered. No commerce policy changed.

Collection finds 1,146 tests without import errors; Ruff and whitespace checks
pass. The rebuilt 85-entry wheel passes a clean dependency-free installation,
both entry points, JSON administration/recovery and temporary listeners. All 80
runtime files byte-match source. Logs:
`results/room-boundaries-wheel-{build,check}.log`.
SHA256: `4f87779302606b4beb5c4b9519eeb085918089836e8d68d56ed32e360667e1c5`.
These checks do not repeat the entire suite, live PostgreSQL or rendered native
UI validation. No native application, original asset or live save was touched.

## Whole-suite checkpoint and native ranking dates, 2026-09-27

The full portable suite ran against clean `995875e`, finishing 1,141 tests in
769.854 seconds: **1,081 passed, 59 optional PostgreSQL tests skipped, one error**.
Log: `results/full-suite-995875e.log`. The manifest
`results/full-suite-995875e-manifest.json` records all 254 tracked source/test/tool
files; their hashes were unchanged throughout the run. The error was a stale
custom-event fixture that assigned a 25-cell board before changing its declared
two-player shape. The newer immutable configuration validator correctly rejects
that construction. Commit `6485f68` declares three players in the same replace
call. All three custom-board tests pass afterward in 33.179 seconds:
`results/custom-event-board-after.log`. Runtime validation was not weakened.

Original ranking display checks exposed a separate presentation mismatch:
the client interprets metadata+C as a shared date anchor for an older weekly
tally and the next update. Our automatic policy records live current-week
prizes and previous-week ratings, so its current-Monday timestamp displays
incorrect dates. Commit `a65f413` emits zero only for automatic weekly metadata,
using the client's existing hide-date branch. Saved period dates, rollover,
scores, denominators, immutable result files and manual historical timestamps
remain intact. The new restored-state regression fails before:
`results/weekly-date-regression-before.log`.

All 52 tests in ranking service/integration, period accounting, weekly rating
policy/projection/integration and local-rating ordering pass in 19.705 seconds:
`results/weekly-date-policy-after.log`. These include encrypted metadata reads,
restart and save-failure rollover controls. Independent review found no issue.
The full suite was not repeated after the fixture and metadata corrections;
the checks above cover their affected paths.

Research `aab622e` passes **58 original-instruction cases**: 40 score boundaries,
nine explicitly reconstructed old date labels and nine actual production date
omissions across RR1/3/4 and all three locale selectors. Full projected rows,
source files and snapshots stay unchanged. Logs:
`results/ranking-display-original.log` and
`results/ranking-display-zero-date-original.json`. See
`research/RANKING_DISPLAY_REVIEW.md` for addresses, image hashes and the explicit
UTC calendar fixture/original literal-PST limitation. Large unsigned scores
expose a separate signed renderer limitation; no saved value or order was
changed to hide it. No native application was launched.

A read-only ranking audit passes four supplemental boundaries: final filename
allocation/exhaustion, owner/selector rejection with a full cache, zero versus
absent historical scores, and reads/deletion while exchange reservations remain
active. Log: `results/ranking-boundary-audit-matrix.log`. No duplicate tests or
extra runtime change were needed for those paths.

Collection finds 1,142 tests without import errors; Ruff/whitespace pass.
The 85-entry wheel passes clean dependency-free installation, both entry
points, JSON administration/recovery and temporary listeners. All 80 runtime
files byte-match source. Logs: `results/weekly-display-wheel-{build,check}.log`.
SHA256: `983b8995e104ad61945f17fe0d899c50036db1937411bf916cf90cddeec2977b`.
This checkpoint does not include a live PostgreSQL or rendered native UI run.

## Pending trade-offer retries, 2026-09-27

Commit `dda421f` preserves exact buffered offer pages when page zero is retried
after later pages. Previously a valid 30-card upload with 14/14/2 pages lost
its 28-card prefix and rejected the final page as noncontiguous, aborting the
exchange. The direct reproduction is `results/trade-page-retry-repro.log`;
the encrypted baseline fails for both participant roles:
`results/trade-lifecycle-boundaries-before.log`.

Changed pages now replace only the pending suffix after cumulative ownership
validation. This also preserves replacement offers with an unchanged first
page. The two new tests pass in 1.654 seconds, covering ownership-failure
atomicity, first/middle-page retries, both replacement forms, full encrypted
confirmation, delayed deck-name editing after the first Get, duplicate Get,
unchanged saves before paired commit and exact restoration afterward:
`results/trade-lifecycle-boundaries-focused.log`.

Twenty-seven existing controls pass in 2.794 seconds:
`tests.test_trade_service.TradeTests`, the encrypted commit/restart and
disconnect-after-first-Get tests, and `tests.test_commerce.TradeCommitTests`.
Log: `results/trade-lifecycle-boundaries-controls.log`. Independent review
checked the identical-prefix replacement ambiguity before accepting the fix.
No additional defect was found in the bounded challenge/settling/edit audit.

Original uploader inspection, documented in `research/TRADE_SERVICE_NOTES.md`,
finds fixed 15-card synchronous uploads and explicit E=0 clear actions, but
no per-offer generation or page acknowledgement. Retry/replacement tolerance
is therefore local policy, not a recovered historical host algorithm. No new
original-instruction probe or native UI run was used.

A separate chat/IRC audit passes 15 existing tests in 8.478 seconds:
`results/chat-roster-boundary-audit.log`. The supported 128-account roster fits
the original 17-page array even at maximum names/counter widths. Existing
source evidence establishes signed counter comparisons without wrap-aware
ordering, but no reliable active-UI capacity denial policy; no new rollover
or rejection message was invented, and no chat runtime change was needed.

Collection finds 1,141 tests without import errors; Ruff/whitespace pass.
The 85-entry wheel passes clean dependency-free installation, both entry
points, JSON administration/recovery and temporary listeners. All 80 runtime
files byte-match source. Logs: `results/trade-retry-wheel-{build,check}.log`.
SHA256: `6bc3712a5c20b48942b92bf18b9cf5602516c4d47364fcb981ed388bac1c9548`.
These focused checks do not claim a fresh whole-suite or PostgreSQL run.

## Generic profile selectors, scalar bounds and transport audit, 2026-09-27

Commit `ac93d52` compares privacy `NO` selectors by parsed numeric identity.
Previously `6`/`06` or `14`/`014` bypassed duplicate detection and overwrote
the same field. Sixteen subcases fail before the fix, covering CR/GR, both
selectors and orderings, and registered/unregistered profiles:
`results/profile-selector-alias-before.log`. The regression passes after,
preserving all earlier edits on rejection and accepting single aliases and
distinct selectors: `results/profile-selector-alias-after.log`.

Restored PO float fields 12–15 now reject text beyond 127 bytes and values
that overflow float32. Source note `research/PROFILE_SCALAR_BOUNDS.md`
(`b140354`) records the original 128-byte terminated parser buffer and
`fstp dword` destination. These are local output safeguards, not recovered
historical formulas or a claim about the original CRT's overflow behavior.
The existing decimal-only syntax remains unchanged. Valid negative values,
signed zero, maximum finite values, subnormals and underflow retain their
exact saved spelling in public responses.

Fourteen rejection subcases fail before the fix: twelve across the four
float fields, plus two startup cases with interrupted-match recovery pending.
The latter now preserve saved bytes and reserved balances on rejection.
Logs: `results/profile-scalar-bounds-before.log`. All 25 tests in
`tests.test_character_profiles`, `tests.test_profile_transactions` and
`tests.test_startup_snapshot_validation` pass in 1.614 seconds, including
28 valid float boundary subcases and encrypted read/edit/restart controls:
`results/profile-scalar-bounds-after.log`. Independent review found no issue.

A separate read-only ProfileServer audit found no defect. Twenty-five existing
tests pass in 9.813 seconds: `results/profile-context-audit-tests.log`.
Six supplemental encrypted scenarios pass: failed-auth cache preservation,
foreign-owner reads/deletes, sliced/EOF reads across IRC reconnect, truncated
authenticated deletes, shutdown during three incomplete framing stages and
fresh-service rejection of stale contexts. Log:
`results/profile-context-audit-matrix.log`. No extra runtime changes or
duplicate tests were added for these already-correct paths.

Collection finds 1,139 tests without import errors; Ruff/whitespace pass.
The 85-entry wheel passes clean dependency-free installation, both entry
points, JSON administration/recovery and temporary listeners. All 80 runtime
files byte-match source. Logs: `results/profile-scalar-wheel-{build,check}.log`.
SHA256: `f0d841d1b23056c763cfb435388272d850eff4169aebca707df9e4151979ce9a`.
These focused checks do not claim a fresh whole-suite, PostgreSQL or native UI run.

## Channel case aliases and authenticated room names, 2026-09-27

Commit `f9e0235` resolves JOIN, PART and channel relay aliases to advertised
room/table/service spellings, or the first spelling of an existing generic
channel. Previously case changes could create separate memberships, block
relay, reject ordinary observer admission or miss event selection cleanup.
This is an ASCII-only local compatibility rule; no IRC punctuation equivalence
or historical transport case policy is claimed. Original table-cache lookup
is byte-sensitive, so advertised spelling remains intact.

- Three encrypted regressions fail before (2.824 seconds) and pass after
  (3.230 seconds): `results/irc-channel-case-{before,focused}.log`.
- Fifteen existing channel/chat/event PART/observer/departure controls pass in
  25.727 seconds: `results/irc-channel-case-controls.log`.
- Nine advertised ordinary/event room/table/E/F/D channel spellings and
  distinct punctuation pairs pass: `results/irc-channel-case-spellings.log`.
  Independent runtime review found no additional issue.

Commit `327c56f` supplies the canonical account name to the shared room DE/PD
validator. The complete fixed-width field is checked before any room mutation;
an invalid sender follows the existing identity-rejection/connection-cleanup
path. No invented acknowledgement is added. Four encrypted forged-name
subcases (ordinary/event, DE/PD) fail before the fix:
`results/room-presence-names-before.log`. The two new tests pass in 4.912 seconds,
including canonical 16-byte synthetic wire names in live deltas and PTL
snapshots, unchanged durable accounts/save bytes, and a surviving witness:
`results/room-presence-names-after.log`. The forgeries differ only at byte 16,
ensuring authentication does not use shortened display names.

Forty-one room/service/event/profile/full-name controls pass in 57.058 seconds:
`results/room-presence-names-controls.log`. The unchanged recorded native
room/table/chat/selection commands still reach the first turn over a fresh
encrypted connection (one test, 1.150 seconds):
`results/room-presence-native-replay.log`.

Original research `624bdba` passes 23 sender/receiver/constructor/cache cases:
`results/room-name-provenance-original.json`. DE/PD names come from CN, distinct
from HN. A full 16-byte name can bleed into following metadata during native
serialization; the receiver reports -8404 and retains an unterminated field.
That existing native limitation is documented in `research/ROOM_NAME_PROVENANCE.md`
and deferred for loader/native review. The server's synthetic 16-byte support
is not evidence of safe native sender or rendered behavior; no suffix trimming
or identity weakening was introduced to mask the client issue.

Collection finds 1,135 tests without import errors; Ruff/whitespace pass. The
85-entry wheel passes clean dependency-free installation, both entry points,
JSON administration/recovery and temporary listeners. All 80 runtime files
byte-match source. Logs: `results/channel-presence-wheel-{build,check}.log`.
SHA256: `2121209fe84bced8137d4593210cfdd81ff96b7290f098d2b5b2956f4ae1920e`.
These focused checks do not claim a fresh whole-suite, PostgreSQL or native UI run.

## Event table chat and native entry-denial cleanup, 2026-09-27

Commit `26e162c` clears exact table chat memberships on participant release and
explicit observer ExitCh. Previously a completed round, active quitter whose
socket remained usable, or exiting observer could leave stale roster entries.
The coordinator emits a transient release fact once per participant from its
shared completion/cancellation path. The adapter detaches chat synchronously
under its lock and sends roster changes through its existing ordered queue.
Room and destination groups remain intact. Finished observer previews are
intentionally retained until their own exit; sender ownership is unchanged.

- Three encrypted regressions fail before (8.614 seconds) and pass after
  (9.239 seconds): `results/event-chat-cleanup-{before,focused}.log`.
- Eleven existing controls pass in 47.438 seconds, covering observer reentry
  and retired writers, neighboring tables, table reuse, full observer rounds,
  proposal retries and delayed old-table PART:
  `results/event-chat-cleanup-controls.log`.
- Four in-memory source scenarios confirm exact once-only release facts and
  reservation cleanup for dispatch Quit, background proposal expiry,
  disconnect and completion: `results/event-chat-release-sources.log`.
- Independent runtime review found no further issue with the release signal,
  exact-group selection or delivery/lock boundary.

Research `8111a26` executes eight original-instruction cases for EventEn:
positive, pending, negative-boundary and cleanup-mode controls. The specific
denial -32870 automatically sends selected-room PC before the rejection notice.
Physical room presence while awaiting that packet does not establish an event
participant or indicate a server defect. See `research/EVENT_ENTRY_DENIAL.md`
and `results/event-entry-denial-original.json` for addresses and execution limits.
Three encrypted audit cases (expired new member, excluded member, existing
post-end member) preserve durable state, remove old chat, then fully clean with
PC while retaining a usable connection: `results/event-entry-denials-audit.json`.
No runtime edit or duplicate regression was needed for this path. Thirteen
admission/selection boundary controls pass in 0.068 seconds:
`results/event-entry-boundary-controls.log`.

Collection finds 1,130 tests without import errors; Ruff/whitespace pass.
The 85-entry wheel passes clean dependency-free installation, both entry points,
JSON administration/recovery and temporary listeners; all 80 runtime files
byte-match source. Logs: `results/event-chat-wheel-{build,check}.log`.
SHA256: `ff680a8c5799767d25881b42f154a2e270c114512758a7ac7c03365b66e1c5d6`.
These are focused checks, not a new whole-suite, PostgreSQL or native UI run.

## Room/table chat departures and full account names, 2026-09-27

Room DE transitions and native PC departures previously left a departed player
in the old chat roster. Commit `0a2ec6a` detaches the exact old group before
notifications. The six failing baseline cases cover ordinary/event moves in
both directions and both kinds of PC exit. All 13 focused checks pass in
13.514 seconds, including delayed old-room exits, reconnects and full names:
`results/chat-room-transition-{before,after}.log`.

The same stale membership occurred after table GameQT, GameExit and host kick.
Commit `a34cece` removes chat membership only for actual removed players and
that exact table, alongside reservation cleanup; notification occurs outside
the match lock. All three baseline subcases fail before the fix, then the new
regression and existing exit/kick/owner-transfer/retry/round/room controls pass:
12 tests in 31.001 seconds, `results/chat-table-exits-{before,after}.log`.
Other chat groups, remaining account reservations and healthy sockets survive.

Full 16-byte saved names were accepted by accounts but rejected by match and
event admission. Both now validate the canonical input before projecting a
15-byte display label for terminated client fields. Full CN authentication and
account/profile bytes remain unchanged. Two new direct regressions fail at the
old admission boundaries: `results/display-name-before.log`. All five new
tests then pass in 5.923 seconds, including complete encrypted ordinary/event
rounds, explicit progress and save/reload: `results/display-name-after.log`.
Invalid suffixes are rejected before projection; two names sharing a displayed
prefix remain distinct. The existing match, event-file, event-room and admission
identity modules pass all 102 tests in 1.365 seconds:
`results/display-name-controls.log`.

The original-consumer probe (`9f77341`) passes 34 cases against actual production
output: PLAYACK, VsGameInit, GameOwner and EventMemberList for fresh/restored
two/three-player accounts with colliding display names. IDs, local seats, owner
flags and own event progress stay distinct, and full profile bytes are unchanged.
See `research/DISPLAY_NAME_IDENTITY.md` for execution scope and code hash;
captured output is `results/display-name-identity-original.json`.

Collection finds 1,127 tests without import errors; Ruff and whitespace checks
pass. These focused checks do not claim a fresh whole-suite, PostgreSQL or
native UI run. The rebuilt wheel contains 85 entries and all 80 runtime files
byte-match source. Clean dependency-free installation, both entry points, JSON
administration/recovery and temporary listeners pass. Logs:
`results/room-name-wheel-{build,check}.log`.
SHA256: `1ffb453b2f724f7004e7c5a4ce93aa3e9800bff5c590f30257ca9221adf80d25`.

## Event progress publication and claim recovery, 2026-09-27

The direct EventResults API could partly advance earlier participants when a
later participant's existing progress ID had conflicting facts. Membership and
step limits were prevalidated, but update receipts were checked while applying
changes. Progress now stages through EventHost and publishes only after every
update succeeds. The production adapter already stages its outer transaction;
this is an in-memory API correction, not a demonstrated saved-state loss.

- The new last-participant conflict regression fails before the fix:
  `results/event-progress-conflict-before.log`. It then passes with exact
  account/journal preservation, valid retry and replay after restore.
- All 20 result and claim-transaction checks pass in 0.073 seconds:
  `results/event-progress-conflict-focused.log`.
- Three encrypted integration checks pass in 13.286 seconds: complete two-human
  and three-human rounds, explicit wins progress, and withheld result/progress
  after a failed save. Log: `results/event-progress-conflict-encrypted.log`.
- Independent review found no issue with staged journal ownership, immutable
  member/admission values or outer persistence ordering.
- A separate 30-test event claim/membership selection passes in 8.174 seconds:
  exact names are in `results/event-claims-persistence-audit-tests.log`.
  Six additional in-memory recovery combinations pass, covering mixed grants
  across nine-card batches, queued grants, capped money, excluded/re-enabled
  members after event end and IDs scoped to accounts/events. Results are in
  `results/event-claims-persistence-audit-matrix.log`. No further defect or
  duplicate tests resulted from that audit.

Collection finds 1,119 cases without import errors; Ruff and whitespace checks
pass. These focused runs do not claim a new whole-suite, PostgreSQL or native UI
pass. The rebuilt 85-entry wheel passes clean dependency-free installation,
entry points, JSON administration/recovery and temporary listeners; all 80
runtime files byte-match source. Logs: `results/event-progress-wheel-{build,check}.log`.
SHA256: `9d379180bf260de1534951169b30a6e1037273f281f831788f165bf73605ea46`.

## VS. Rating producer and neutral hosted controls, 2026-09-27

Original probe `ade40fb` passes 93 cases: 72 complete offline result/presentation
cases, 20 hosted profile import/ResultData receive/presentation/export cases,
and one constructor store. The offline producer supplies random signed changes
based on unique result ordering; the original formatter removes the second
component for tied/losing classes. These are client-local rules, not recovered
historical hosted selection or scheduling.

Hosted neutral `T=0|0|count` preserves imported RT totals 0, 1, INT_MAX,
0x80000000 and UINT_MAX through the actual presentation states and export.
VS. Player Games and Prize Points remain independent. No runtime fix is needed.
See [the scoped evidence](research/VS_RATING_SOURCES.md), reproducible probe and
`results/vs-rating-sources-original.json`. Only supplied random outputs and final
drawing/runtime boundaries are intercepted; the consequential rating formatter
executes. No native application or original asset writes were involved.

## Auction collection follow-up, 2026-09-27

No query, settlement or recovery defect was found. Existing regressions cover
the audited contracts; no duplicate tests or runtime changes were added.

- All 12 auction service checks pass in 0.037 seconds, including immutable
  query files, filtering, account-scoped cleanup and queries during collection:
  `results/auction-query-followup.log`.
- A focused 36-test core/transaction/integration selection passes in 4.238
  seconds. Exact test names are in `results/auction-settlement-audit-tests.log`.
- Thirteen additional in-memory cases pass, recorded in
  `results/auction-settlement-audit-matrix.log`: combined seller payment and
  outbid refund at the money cap retain the same residual receipt across three
  restarts; expiry during all four collection phases stays outside that visit's
  frozen claims. Completion, disconnect and restart preserve exact later
  payment/card collection and reservation identity. Money and cards conserve.

These are focused checks using disposable data, not a new full-suite,
PostgreSQL or native UI run. The last verified wheel remains unchanged.

## Memorable Win sources and progression follow-up, 2026-09-27

Original probe `addf270` passes 458 cases, watching every byte of the RN buffer
during the tested result, profile import and ResultData receiver paths. Its
430 ordinary offline score/seat combinations and ten online controls preserve
RN; classification, rating and sequence checks confirm execution. Three profile
imports and 15 rotated receiver cases identify the writers and local-seat
selection, including omitted/empty/supplied RN. This establishes no automatic
Memorable Win selection rule. See [the scoped evidence](research/MEMORABLE_WIN_SOURCES.md)
and `results/memorable-win-sources-original.json` for reproduction and code hash.

The existing ranking-period, weekly policy and projection batch passes all
22 tests in 0.502 seconds: `results/progression-followup-audit.log`. Inspection
found no additional progression defect, and no duplicate tests were added.
Deferred review now points to the packaged entry points and actual example
paths; both CLI help commands and its three documentation links were checked.
No runtime changes, full-suite rerun or native application launch were made;
the wheel checked for the previous event-board fix remains current.

## Event board startup validation, 2026-09-27

Over-blocked event boards previously passed JSON parsing and room publication,
then failed when matchmaking built the round, releasing and disconnecting the
selected players. EventTableConfig now validates board dimensions, native tile
domains, the existing non-CBC restriction and five available placements per
seat. Native start_board classifies capacity, so placeable F3/4/5 effects are
retained. Seed semantics are shared with JSON, and mutable input boards are
copied to an immutable tuple before publication.

- Four new configuration regressions reproduce 25 failing subcases before the
  fix: `results/event-board-config-before.log`.
- All 33 configuration, event round and coordinator checks pass in 0.120 seconds:
  `results/event-board-config-focused.log`. These include completed two-human,
  three-human and synthetic CPU rounds at the exact placement limit, caller
  mutation after configuration, both unsigned seed endpoints and placeable
  special tiles.
- Independent review found no missing supported engine constraint. A separate
  lifecycle audit passed 23 existing tests in 29.229 seconds and 14 additional
  in-memory cancellation cases. Refreshed reservation tokens are released,
  neighboring active tables are unchanged and repeated cleanup is inert. No
  new defect or duplicate test was added from that audit.
- Collection finds 1,118 tests with zero import errors. Ruff and whitespace
  checks pass. This is focused validation, not a complete-suite, PostgreSQL or
  rendered native run.

The rebuilt 85-entry wheel passes dependency-free clean installation, console
and module entry points, JSON administration/recovery and temporary game/profile
listeners. All 80 runtime files byte-match source. Logs:
`results/event-board-wheel-{build,check}.log`. Wheel SHA256:
`7e6c2e334ffa81fb59c1a831c80d96a63160e59c7d70481f9ca3e4951e9ad037`.

## Rematch eligibility and recovery follow-up, 2026-09-27

No runtime defect was found. The new encrypted regressions in `909bef5` preserve
four complete scenarios: a prize loser has fewer than five cards, or lacks the
doubled next stake, with each participant making the first Continue vote. Direct
final-vote rejection leaves match/RNG, prior votes, pending rewards, balances,
reservations, room, round ID and saved bytes unchanged. The network rejection
then exercises the existing disconnect/resync policy, releases both participants
and preserves the exact card multiset across inventory and recovery. Restart
matches the recovered state without another write. Both tests pass in 11.913
seconds; these cover business rejection separately from existing save failures.

Original constructor, collection counter, choice and sender execution in
`45401b4` passes 14 eligibility cases plus explicit leave. Normal Conf0 emits no
request with fewer than five cards or insufficient doubled/capped stake. Computer
Conf1 still requires five cards but allows a cheaper setup without funding the
previous wager. No new negative response is established. See
[Continue evidence](research/CONTINUE_ELIGIBILITY_NOTES.md); output is
`results/continue-eligibility-original.json`.

The old underfunded-start test now admits both players before the owner raises
the wager, so it still reaches final start authorization after the entry fix.
All 21 related prize, wager and privacy tests pass in 53.398 seconds:
`results/rematch-prize-wager-followup.log`. Collection finds 1,114 cases without
import errors; Ruff and whitespace checks pass. This is focused validation,
not a full-suite, PostgreSQL or rendered native run. No runtime files changed;
all 80 runtime files in the checked wheel byte-match current source.

## Authoritative table-entry restrictions, 2026-09-27

The adapter previously admitted new participants without the original menu's
card-count, collection-point and wager checks. It now validates the committed
account before reserving it. The check follows the separate occupied-human,
empty-fixed, empty-custom and Computer entry paths; admitted members and
spectators remain exempt. Failed Computer admission also preserves the existing
match generation. The existing mapped nonfatal errors are an explicit local
compatibility choice, not recovered historical per-condition replies.

Original fixture `018d201` contains 31 passing instruction cases using production
metadata and the original PTL projection/Join predicate. See
[eligibility evidence](research/TABLE_ELIGIBILITY_NOTES.md) for addresses, exact
boundaries and reproduction. Output: `results/table-eligibility-original.log`.

- Nine in-memory rejection subcases fail before the guard, with the eligible
  retry case already passing: `results/table-admission-rules-before.log`.
- The encrypted regression `1dd3361` also fails before the fix. With the fix,
  both GameEN and Req reject without changing match, room, reservations, economy
  or saved bytes; the same connection joins after CP bounds are relaxed exactly
  to its committed value.
- All 25 initial admission/roster/identity/fixed-room cases pass in 17.055 seconds:
  `results/table-admission-focused.log`.
- All 18 Computer startup, captured native traffic, session recovery and observer
  cases pass in 11.409 seconds: `results/table-admission-boundaries.log`.
- The final six new checks pass in 1.351 seconds, including the encrypted case,
  unchanged match generation and a cardless observer:
  `results/table-admission-rules-final.log`.

These are 45 distinct focused checks, not a new complete-suite or PostgreSQL
run. Collection finds 1,112 tests without import errors. Ruff and whitespace
checks pass. No native UI, original assets or live saves were used.

The rebuilt 85-entry wheel passes a clean dependency-free install, both entry
points, JSON administration/recovery and temporary game/profile listeners. Logs:
`results/table-admission-wheel-{build,check}.log`. SHA256:
`074541968e62e1558b6c50fc38aab0709770bcf0c4b14a71842bf1e646e7e93d`.

## Original table-query audit, 2026-09-27

Original instruction probes pass for both Rule lobby-event branches, the
current-table sender, response/cache export, CheckJoin sender and all four
Boolean reply combinations. Every nonzero combination selects recovery cleanup
and sends ExitCh; the probe stops before the later channel transition. No
individual-bit seat or membership meaning is established. The existing ownership
guard has no demonstrated compatibility defect from these paths.

Commit `04fbb4d` preserves the probe and
[scope/reproduction notes](research/TABLE_QUERY_NOTES.md). Output is retained in
`results/table-query-original.log`. This uses isolated original instructions
with synthetic state and substituted widget/transport boundaries; no native UI
or network was exercised. There is no runtime change or new portable fixture.

## Portable NPC test migration audit, 2026-09-27

Restored an asset-independent NPC API check that had been excluded together
with its original instruction-dependent test class. Portable tests now directly
check reserved selectors, known NPC names, invalid guilds and name visibility
at collection-point boundaries. Three other empty migration test shells were
removed; their original instruction tests remain archived under research/tests.

All six focused NPC API/batch tests pass in 0.017 seconds. The batch checks
retain original hand, unused-card and RNG-stream comparisons. Collection finds
1,106 tests with no import errors; Ruff and whitespace checks pass. No runtime
files changed, so the checked wheel below remains current. This was not a
full-suite or original-instruction rerun.

## Native Computer room rule compatibility, 2026-09-27

Original constructor/Init/editor/sender execution found a real gap in optional
fixed CPU rooms. The hosted Computer constructor always sets departure rule
`gs=0`; ComGameInit carries no override and the Computer editor has no departure
control. A preset requiring gs1/2 therefore rejected an unchanged valid native
start. Public room configuration now requires gs0 for fixed rooms containing
any CPU table, including an additional CPU table in a mixed room. Human-only
presets and the shipped custom CPU room retain their behavior.

Six original instruction chains (`52611e3`) also establish that personal saved
rules do not replace Computer Init values. Fixed-room context does not lock the
Computer editor. Opening that optional editor clears double-up; direct opponent
selection preserves it. These boundaries are documented in
[Computer room rules](research/COMPUTER_ROOM_RULES.md), with visible-editor
limitations retained on the review list.

- Both new configuration regressions fail before the guard (five subcases).
- All 20 rule/configuration/room/lifecycle checks pass in 2.278 seconds:
  `results/computer-room-rules-tests.log`.
- All ten configuration and mocked-launcher checks pass in 8.254 seconds:
  `results/computer-room-rules-launch-tests.log`. No game was launched.
- The final three new checks pass in 0.598 seconds, including an encrypted
  replay of the original gs0 start through actual card selection/StartData,
  with different personal defaults preserved:
  `results/computer-room-rules-native-wire-tests.log`.

The rebuilt 85-entry wheel passes clean dependency-free installation, entry
points, JSON administration/recovery and both temporary service listeners.
All 80 packaged runtime files byte-match source. Logs:
`results/computer-room-rules-wheel-{build,check}.log`. SHA256:
`76c0f340f07cd5225bfe0520b8b76918ee5995cff1461542d206897ce1d325b0`.
This remains focused validation, not a new full-suite, PostgreSQL or rendered
native run. Collection finds 1,104 cases without import errors. Ruff and
whitespace checks pass. The ten rule unit cases also pass after their valid
CPU examples were aligned with native gs0.

## Fixed-room editor and lifecycle follow-up, 2026-09-27

No new runtime defect was found after `ef6db73`. Original instructions now trace
the initial GameEA consumer, table-rule/context copies, editor event/constructor
selection, saved preference initialization and Apply sender for Open Tables,
Classic, Special and Quick Play. Fixed rooms skip the game-rule editor and
retain the advertised preset; Open Tables uses saved game defaults. Personal
table options remain usable in both. The four actual original Apply payloads
are frozen in `tests/fixtures/fixed_room_apply.json`; the scope and reproducible
procedure are in [the editor evidence](research/FIXED_ROOM_EDITOR.md).
The original trace and independent Rule13/current-editor control are committed
as `65b64cf`; execution output is `results/fixed-room-apply-original.log`.

All six focused integration/lifecycle cases pass in 12.667 seconds
(`results/fixed-room-lifecycle-followup-tests.log`):

- Replay all four original Apply messages through encrypted connections,
  matching complete directory/PTL fixture hashes and preserving saved defaults.
- Move one account through Open, Classic, Special, Quick and back to Open with
  other accounts still present, then restart from its temporary save. Custom
  defaults remain usable and room presets reset independently.
- Complete fixed CPU rounds through capped double-up (1250 to 2000), rematches,
  two/three-seat reconfiguration and leave/reset. Reject conflicting settings
  without changing match, economy, room or RNG state.
- Complete a human round/rematch and exercise each configured departure rule.
- Retain the two preceding encrypted fixed-room startup/conflicting-Apply cases.

The two new lifecycle cases plus 15 existing wager/departure checks also pass
separately (`a18b933`). This is additional focused coverage, not a full-suite or
PostgreSQL rerun. Collection finds 1,101 tests without import errors; Ruff and
whitespace checks pass. There are no runtime changes or new dependencies; the checked
wheel and its hash below remain current. Original instruction probes use
synthetic memory and widget/send boundaries, not native rendering or input.

## Room discovery, Computer availability and presets, 2026-09-27

The original client requires ASCII `A` at RL room-record `+0x29` before it
enables Computer. The old directory left this byte zero. The corrected
advertisement now enables Local Room and leaves human tables explicitly
human-only. [Original evidence](research/ROOM_COMPUTER_GATE.md) includes 12
Computer cases, two table-prefix comparisons and all five actual launcher
rooms through original RL/PTL parsing, selected-room copying and action checks.
Local Room enables Computer with zero money and 25 cards; the other rooms
retain human Join. The fixture freezes the directory and table-file hashes.

The launcher now supplies Open Tables plus fixed Classic, Special and Quick
Play rooms. Their engine rules, directory fields, empty-table metadata and
reset state agree. Configured rules survive Computer-menu replacement too;
personal saved defaults remain separate. These presets and their enforcement
are explicit local policy. See [room configuration](ROOMS.md).

Focused validation passed:

- 131 rules, engine, settings, Computer and board checks (`e247d04`).
- 41 room, directory, shared-table and launcher checks in 86.364 seconds
  (`results/room-presets-adapter-tests.log`).
- 31 admission, captured startup, transition, metadata, CPU and snapshot
  checks in 22.824 seconds (`results/room-presets-admission-tests.log`).
- Six new discovery/configuration cases in 1.421 seconds: original production
  fixture equality, five-room lifecycle, mixed human/CPU tables, invalid
  configuration, encrypted directory-to-two-NPC entry, and nonfatal rejection
  of Computer admission to a human table
  (`results/room-presets-discovery-tests.log`).
- Two encrypted fixed-room cases in 3.134 seconds (`b6c4547`): saved defaults,
  rule query/Apply, native-order human startup and generated special board;
  conflicting Apply leaves the preset and economy intact while an unrelated
  client remains healthy. This tests protocol sequencing, not native editor UI.
- Independent review found no actionable defect; an in-memory fixed CPU probe
  preserved identical directory, table and engine rules through three-seat
  configuration, Quit and reset.

Current collection finds **1,097 tests** without import errors. This iteration
does not repeat the full suite or real PostgreSQL tests. Ruff and whitespace
checks pass. Test saves and listeners were temporary; the native game and live
save were not opened or modified.

The 85-entry wheel passes a fresh dependency-free installation, entry points,
JSON administrative/recovery commands and both listeners on temporary ports.
All 80 packaged runtime files byte-match source. Logs:
`results/room-presets-wheel-{build,check}.log`. SHA256:
`35e91e2f233f63c0c2c05888413eea3eb3d5fd92994183d511786d1b921e0bc3`.
Visible menus and actual native input remain on the native checklist.

## Two-NPC Computer menu checkpoint, 2026-09-27

The complete suite against an isolated archive of commit `f2306f8` finished
**1,075 tests in 978.914 seconds: 1,016 passed, 59 optional PostgreSQL checks
skipped, and no failures**. Output and source metadata are in
`results/three-player-computer-complete-suite.log` and
`results/three-player-computer-complete-suite.json`. The child process imported
only the archived source and had no database URL in its environment. It used
temporary saves and headless encrypted peers; the temporary source directory
was removed. Local Play's game-window messages came from mocked launcher tests,
not native game execution.

Focused checks already pass:

- Original one-human/two-NPC menu, independent personalities, full games,
  rematches, two/three-seat reconfiguration and result choices: 136 engine and
  surrounding checks (`55da252`). Three frozen sender vectors preserve original
  instruction evidence.
- Both NPC roster entries, distinct identities, sibling-table isolation,
  one-human admission and target-count stake debit: 52 surrounding checks and
  18 final adapter/room checks (`c6ec25e`).
- Five encrypted gameplay/persistence cases in 8.717 seconds (`f2306f8`):
  default practice, optional wagers/card prizes/ratings, reconfiguration,
  rematches, once-only refunds, actual earned-card recovery and failed-save
  atomicity. Other accounts remain unchanged.
- Six encrypted observer checks in 18.700 seconds (`1732f89`), including the
  new one-human/two-NPC game. Four original receiver probes additionally accept
  early/late production snapshots at both NPC name-visibility thresholds.
- Independent review: all 435 distinct NPC pairs fit initial reply frames;
  all six finishing orders complete prize choices and a rematch; cancellation
  refunds once, removes both NPCs and releases the account. No additional
  production change was needed.
- Peer-control spoofing (`424a741`): 36 related tests in 47.103 seconds,
  including 52 rejected payload variations, alternate selectors, both IRC
  verbs, and preserved valid chat/trade. The forwarding defect was reproduced
  before the fix. See `results/peer-service-spoofing-related-tests.log`.

After that archived checkpoint, `395cb24` corrects two-NPC deck preparation:
both ten-card pools are generated before either hand is selected. Eight frozen
original-instruction cases match every represented card field, the complete
random-draw digest and final state. All 120 focused NPC/engine/AI/board/prize
tests pass, as do eight encrypted Computer/observer/persistence checks in
18.416 seconds (`results/computer-batch-encrypted-tests.log`). Another 120
single-opponent comparisons against the preceding implementation are unchanged.
The encrypted practice helper now correctly expects zero rewards when wagers
are disabled, including a human win (`dbf39f4`); server policy is unchanged.
Current collection finds 1,079 cases with no import errors. The full archived
run does not include these four new fixture tests or the deck-order correction.

The final 84-entry wheel passes a fresh dependency-free install, all entry points,
actual JSON award/account/recovery commands and both service listeners on
temporary ports. All 79 packaged runtime files byte-match source. Logs:
`results/computer-batch-wheel-{build,check}.log`. Wheel SHA256:
`4119bc087dd9d663c3ee00fadf984ae868d501d2d4f08fac28cf4c8542a9014e`.
Repository-wide Ruff and whitespace checks pass. Real PostgreSQL checks were
not repeated in this iteration; the previous isolated 59-case pass is below.
Native rendered-menu checks and historical hosted policy equivalence remain
separate from this validation.

## Earlier resumed checkpoint, 2026-09-27

An isolated archive of commit `5ee9e81` completed **1,030 tests in 1,181.037
seconds: 969 passed, 59 optional PostgreSQL cases skipped, and two failures**.
Both failures came from spectator-test setup applying settings after Req had
already begun the frozen start handshake. The captured client sequence is
GameEN, Apply, GameReady, Req, GameOK. The helper now follows that sequence and
checks the advertised observation rule before acceptance; both failed cases
pass. Production rules were not relaxed. The broader observer/startup/captured
traffic rerun passes all 65 checks in 125.977 seconds (`46d6de5`). All 971
non-database cases from the complete run have therefore passed across that run
and the targeted correction; the complete run was not repeated afterward.

Output and the exact source commit are in `results/resumed-complete-suite.log`
and `results/resumed-complete-suite.json`. The temporary source directory was
removed. The run used disposable saves and headless peers, with optional
database URLs removed from its environment.

Subsequent changes have separate focused checks:

- Unknown journal/receipt fields are rejected before recovery writes: 128 checks
  pass, preserving supported legacy formats (`cab0f83`).
- Each of eight collection-response interruption points preserves credits and
  uncollected cards while another player remains active: 31 checks pass
  (`f8cc389`, tests only).
- Runtime log I/O failure no longer interrupts replies or cleanup, while state
  failures still stop mutations: 79 checks pass (`8b17927`).

Current collection finds 1,048 tests with no import errors. The complete
1,048-case suite and real PostgreSQL checks have not been repeated. The 84-entry
wheel passes a fresh dependency-free install, actual JSON award/account/recovery
commands, all entry-point checks and both service listeners. All 79 packaged
runtime files byte-match the current source. Logs:
`results/resumed-final-wheel-{build,check}.log`. Ruff and whitespace checks pass.

## Earlier complete-suite checkpoint

The complete suite at commit `fd56461` finished **988 tests in 842.506 seconds:
929 passed, 59 optional PostgreSQL cases skipped, and no failures**. This uses
the final production code from `77cf376`. The 59 database cases also passed
separately against a new isolated PostgreSQL 16.15 host, as detailed below.

Output is preserved locally in `results/overnight-final-complete-suite.log`.
The run uses disposable saves and headless peers. Local Play's messages about
game windows come from its mocked launcher tests; no native game was launched.
The final wheel and repository-wide Ruff checks pass, all temporary database
resources were removed, and the working tree is clean at this checkpoint.

Native presentation checks and historical hosted reward-policy equivalence
remain separate from this validation.

## Subsequent operator-command additions

Commits `861ef3d` and `d618485` add read-only account/reward inspection and
explicit event-progress administration. All 54 admin/event/prize checks pass
in 2.178 seconds. They cover remaining rewards after a real partial collection,
unchanged saves on inspection, account ownership, progress replay/conflicts,
invalid or excluded members, and retry after failed persistence.

Collection now finds 992 cases without import errors. The full 992-case suite
and real PostgreSQL checks were not repeated after these scoped changes.
The updated 82-entry wheel passes a clean install without optional dependencies,
actual JSON reward and account-inspection commands, entry points and both service
listeners. Logs: `results/event-progress-wheel-{build,check}.log`.

## Recovery preview and admission timing

The read-only `tmserver-admin recovery` command passes 93 recovery/admin/economy
checks. Six new cases compare its forecast with actual headless server startup,
including interrupted wagers, earned cards, accepted rewards and unrelated
auction refunds. Repeated previews preserve the input and saved bytes. Output:
`results/recovery-preview-tests.log`.

Ordinary settings freeze at the start handshake (`36734cf`), with identical
retries and saved defaults still supported. All 141 scoped checks pass, including
captured startup traffic and delayed invitations. Event admission checks expiry
before processing ready messages (`4de3c34`); all 62 scoped checks pass, including
event-end and oldest-admission boundaries, continuing active games and later
reward claims.

Collection finds 1,010 cases without import errors. The complete suite and real
PostgreSQL checks have not been repeated after these changes. The 83-entry wheel
passes a clean stdlib-only installation, actual JSON award/account/recovery
commands, and both service listeners. The recovery command leaves the temporary
save byte-identical. Logs: `results/recovery-preview-wheel-{build,check}.log`.
Repository-wide Ruff and whitespace checks pass.

## Earlier overnight checkpoints

The complete portable suite at source commit `db62eeb` passes: **952 tests in
790.990 seconds, 896 passed and 56 optional PostgreSQL tests skipped**. Output is
preserved locally in `results/overnight-complete-suite.log`. This includes the
new weekly rankings, auction registration history, multiplayer cancellation,
event-room departure, and earlier protocol/recovery fixes in
[the work log](WORK_LOG.md). The run uses temporary saves and headless clients.

The 80-entry wheel also passes a clean dependency-free install, console/module
entry points and startup of both services on temporary ports. PostgreSQL and
native game-screen validation were not repeated in this checkpoint.

After that full run, the exact event-table departure fix passes3 new encrypted
regressions and44 surrounding event tests. All3 regressions fail against the old
handler. The operator commands, shared JSON lifetime lease and extracted snapshot
validator pass97 related tests in21.94 seconds, including all16 new admin tests
and10 lease tests. Collection now finds981 cases without import errors; the full
981-case suite has not been repeated after these scoped follow-ups.

The final82-entry wheel passes a fresh dependency-free install and an actual
temporary JSON award through `tmserver-admin`, as well as both service listeners.
The local editable environment was refreshed to expose the new command.
PostgreSQL administration is checked separately below. Native reward-screen
observation remains deferred; no real save or game window was used.

The subsequent PostgreSQL 16.15 run passes all 56 existing backend cases in
55.382 seconds and three new admin cases in 1.826 seconds. These cover actual
award redemption/replay, event grants and eligibility, configuration validation,
preserved weekly ranking data, and competing-writer rejection. The host uses a
new private temporary daemon/container and cached image, with loopback access;
it does not connect to a pre-existing user database. Its container, private
daemon, mounts and data directory have been removed, and the test port has no
listener. See the [database validation report](research/ADMIN_POSTGRES_VALIDATION.md).

The Continue/privacy rematch correction passes 42 focused cases, including
encrypted first/final-voter deck updates, stakes, ownership, reload, unrelated
profile changes and save failures. Those 42 cases also pass after replacing
the private accepted-result tuples with immutable named records. Another 38
CPU, three-player, wager, prize and ranking cases passed against the privacy
fix before that representation-only change. Collection finds 988 tests without
import errors; the final complete run is recorded above.

After those final production changes, the rebuilt 82-entry wheel again passes
a clean install without optional dependencies, the actual JSON admin award
command, both entry points and both service listeners. Logs are
`results/overnight-final-wheel-{build,check}.log`.

## Integrated local launch, 2026-09-27

The packaged server now owns the native clients' lifetime through `Play.cmd` /
`tmserver-play`. A shared async service context is used by both standalone server
startup and local play. Free endpoint ports and distinct account IDs are passed
to the sibling `tmloader`; JSON remains explicit. Each run has separate logs.

Six new launcher tests cover three real headless subprocess peers, encrypted
game/profile requests and durable settings, read-only check mode, failed loader
checks, partial startup cleanup, failed second-child creation, exclusive session
ownership and environment isolation. These peers do not load original DLLs.
The combined launcher, configuration and ordinary-room regression passed all
21 cases. The updated 79-entry wheel passed a clean dependency-free installation,
including `tmserver-play --help` and bounded startup of both services.

The actual prepared configuration also passed the real loader's `--check` for
all three generated account/endpoint command lines. A temporary copy of the
imported save passed a real encrypted game/profile exchange through both
packaged listeners. All 133 copied runtime files matched their sources. No game
window was opened by these checks and the real imported save was unchanged.

Native title, room, CPU/rematch, shop/trade and reward validation remains awaiting
user observation in [NATIVE_TEST_CHECKLIST.md](NATIVE_TEST_CHECKLIST.md).

## Packaging checkpoint

Verified on Windows with Python 3.13.15. The portable suite collects 855 cases;
56 require PostgreSQL and are skipped when `TM_TEST_DATABASE_URL` is absent.

The broad run completed in 665 seconds: 797 passed, 56 skipped and two failed
because they still referenced the old research layout. Both failures were fixed
by extracting original-client expectations into portable fixtures. Their
targeted rerun passed, alongside four event-configuration checks. Thus all 799
non-database cases have passed, across the broad run and the targeted rerun;
the entire suite was not repeated after those fixture-only corrections.
The 68 tests consuming the retained protocol fixtures also passed after removing
155 unused copies of scratch research data.

All 56 PostgreSQL cases passed separately against an isolated PostgreSQL 16
instance. Coverage includes joint account/journal rollback, concurrent-writer
rejection, lost connections, lost commit acknowledgements, encrypted service
requests, reload and exactly-once claim recovery. Each test creates and drops
only its own temporary database.

The changed transaction paths also passed a focused 60-case run covering
commerce, matches, claims, profiles and JSON failure handling. The original
source comparison is described in [migration review](../research/MIGRATION.md).

The wheel includes 78 entries, including all three runtime data files and the
Alembic revisions. It was installed with `--no-deps` into a fresh virtual
environment outside this checkout. Both the console and module entry points
worked; both loopback services started and stopped on temporary ports. No
research directory, editable installation or SQL dependencies were needed.

Ruff checks pass for `src`, `tests` and `tools`. Standard test/build/load commands
are in the root README. Detailed run output is generated under ignored
`results/`. Native desktop testing and historical online-policy equivalence
are separate from these checks; no desktop interaction was performed here.
