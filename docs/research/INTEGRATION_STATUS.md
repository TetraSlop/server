# Local implementation checkpoint — 2026-09-26

The default JSON save now detects files that disappear or change outside the
current writer, and rejects stale or overlapping commits. Unique temporary files,
file flushing, atomic replacement and a short OS lock protect each commit;
unchanged snapshots skip replacement. Nineteen new tests cover file/process
failures, encrypted-client shutdown and auction recovery. Storage tests also pass
on Linux. The review patch, full regression counts and source hashes are in
[`../json-store-checkpoint/REPORT.md`](../json-store-checkpoint/REPORT.md).
The save format and common storage interface are unchanged.

The remaining live persistence paths now stage before publication: auctions
(exhibit, bid, expiry and collection), Prize Center awards/purchases, event
membership/claims/results, character profiles, options, initial guild selection,
saved table settings, event deck edits and account creation. Live request
handlers no longer invoke a whole-server save. The remaining production call to
`Server.persist` is startup recovery before listeners open; explicit trusted
administration can also use it.

This pass passed **333 distinct tests**, including **38 new tests**: 274 combined
domain/protocol regressions and 59 real-PostgreSQL/configuration checks. The
disconnect failure test was strengthened to inject at the current save boundary
with an active reservation, and passed a separate two-test rerun. Review patch,
source hashes, AST checks and test IDs are in
[`../claims-transactions-checkpoint/REPORT.md`](../claims-transactions-checkpoint/REPORT.md).
The local save is byte-identical, and the isolated PostgreSQL container and its
private daemon are stopped. No native game or desktop control ran.

Journals remain whole documents and database I/O remains synchronous. Operation
receipts and account ownership would need further work for multiple writers;
these changes do not establish load capacity or historical protocol completeness.

Ordinary matches now stage affected accounts and their settlement/reward journals
before committing. Stakes, result growth/statistics/ratings, prize transfers,
Continue and delayed result edits, abort refunds and returned-card recovery use
the same explicit account/journal boundary. Live accounts, journal state and table
capabilities publish after the save succeeds. A failed writer leaves recovery to
a fresh server, including when a database COMMIT succeeds but its reply is lost.

This pass passed **281 distinct tests**, including **19 new tests**. Real
PostgreSQL tests cover joint account/journal rollback, lost acknowledgements for
stake/result/prize/Continue, and repeated startup recovery. An encrypted full
match with stakes, prizes and ratings rejects any use of the full-save path.
Two- and three-player, CPU, privacy, departures, timeout and multiroom regressions
also pass. See [`../match-transactions-checkpoint/REPORT.md`](../match-transactions-checkpoint/REPORT.md)
for the review patch, source hashes and deduplicated test manifest. The original
local save is unchanged; the temporary test database and private daemon are stopped.

That earlier pass left auction, Prize Center and event claims on the full-save
path; the checkpoint above now covers them. No native game launch or GUI
validation was performed in either pass.

Commerce transactions now stage affected accounts and publish live state only
after storage commit. Ordinary shop commands, final trade Get, and delayed
trade deck edits use scoped account writes. PostgreSQL neither builds nor
compares a world snapshot on these paths; negotiation and duplicate trade Get
do not write. The normal launcher has an explicit `--database` option, with JSON
remaining the default.

This checkpoint passed **325 tests**: 287 domain/economy/protocol regressions
(including 10 new staging tests), 33 real-PostgreSQL/configuration tests
(including 10 new scoped-write tests), and 5 launcher tests with process launch
mocked. Formatting retained all 64 existing EconomyService-module function
bodies; the only trade function changes inject the commit boundary. Review
artifacts are in [`../commerce-checkpoint`](../commerce-checkpoint/REPORT.md).
Native GUI verification remains in [DEFERRED_REVIEW.md](DEFERRED_REVIEW.md).

The initial persistence checkpoint added an optional PostgreSQL path with a two-method storage
boundary, SQLAlchemy Core transactions and Alembic migrations. Final verification
passed **275 tests**: 23 new database/configuration/protocol tests and 252 existing
economy, recovery and encrypted-service regressions. A copy of the actual local
save passed CLI migration, native-profile verification, bounded headless server
startup/shutdown, and lossless JSON export. The original save's SHA-256 is
unchanged. See [DATABASE.md](DATABASE.md) and the review/test artifacts in
[`../persistence-checkpoint`](../persistence-checkpoint/README.md).
The temporary PostgreSQL container and its private daemon were stopped.
The graphical launcher still uses its existing JSON save.

The clean complete checkpoint passed **880 tests in 1010.618 seconds**, with no
failures or skips: test_checkpoint_880_results.txt. It includes parallel event
tables, independent observers, the expanded account pool, room migration and
reconnect cleanup, room-PC departure, table/Viewer/ExitCh controls and startup
watchdog deadlines. Its source hashes are in validation_manifest_handle_identity.json.
test_results.txt holds the most recent complete-suite run.

Subsequent roster/admission changes passed32 focused tests in43.356s:
table_admission_roster_final_results.txt. Those changes are included in
the880-test checkpoint. Twelve original-code fixtures cover delivery timing,
the native Start gate, manual member inspection and recognized admission
failure paths; encrypted server roster/PTL output also passes the Start gate.

Later handle/observer compatibility work passed172 tests in144.988s:
handle_identity_verified_results.txt. The archived native client now replays37
unchanged room/table/chat/selection commands through current encrypted services.
Before the fix it disconnected at GameEN because HID0 was incorrectly compared
with character1. Full native Req metadata is now accepted for observers too.
Original execution verifies the three independent handle serializers. The880-test full suite includes these changes and is recorded in
validation_manifest_handle_identity.json.

Two additional standalone replay checks pass:9 original TM receiver paths
consume the current replay replies, and18 retained native profile operations
succeed over fresh encrypted TCP. Three status bodies absent from the old log
are excluded explicitly. See ../protocol_map/NATIVE_CAPTURE_REPLAY.md; these
standalone checks are separate from the full-suite test count.

Post-checkpoint work: ordinary human rounds now advance the original VS. Player
Games WORD, including wrap, with atomic profile/result consistency. CPU and
borrowed event profiles retain the count. Focused validation passed307 tests:32 unit/original-counter checks,227 engine
regressions and48 encrypted integration cases. Results and unchanged source
hashes are recorded in validation_manifest_vs_player_statistics.json.
See ../protocol_map/RESULT_STAT_LABELS.md for recovered display/storage details.

Further whole-round packet validation passed:398 actual encrypted-server replies
were accepted by continuous original client receivers across two rounds each
for two/three humans. Original code emitted130 correct turn acknowledgements.
This is packet-consumer coverage, not a native graphical run; see
../protocol_map/FULL_MATCH_CONSUMERS.md.

Automatic table sizing is now available with --players auto or room/table JSON
players:"auto". Start freezes two/three humans and selects16/25cells; late joins
receive nonfatal rejection. Exact-count configurations retain their behavior.
Five original receiver cases and two bounded CLI starts pass. Automatic-seat
regressions passed225 test executions across flexible_seats_regression_results.txt
and flexible_seats_engine_results.txt, before the later invitation fix.
See AUTO_TABLES.md and validation_manifest_flexible_seats.json.

Start invitations now wait for a newly admitted guest's GameEA cleanup barrier,
in both fixed and automatic tables. Five original-code cases reproduce the
dropped early invite and validate the current roster/invitation sequence through
the complete native controller tick. The combined latest regression passes72
tests in90.190s, with311 Python sources unchanged during validation. Results
are start_invitation_fixed_auto_results.txt and validation_manifest_start_invitation.json.

## Implemented behavior

- Complete native admission bodies for ordinary, Computer and event players
  and observers. POL handle metadata cannot select a character or inventory;
  authenticated session, nickname and account name preserve that binding.
- Observe Possible/Impossible/No Comments Allowed feeds the original watch
  gates and chat-panel selection. Computer-menu observers use the client's
  own30-NPC names and viewer-level thresholds, including long asset names.
- Full/busy rejection codes now execute the original localized message path;
  the server keeps the connection usable for a later retry.

- Human table roster pushes wait for each client's GameEA cleanup barrier.
  Lobby member inspection works without claiming a seat. Valid full/busy-table
  requests receive mapped failure replies while keeping their connections;
  simultaneous last-seat requests admit exactly one account.
- Pre-start host kicks and configured human-lobby self departure/host transfer,
  preserving remaining reservations and metadata. Ordinary startup cancellation
  and Viewer notices are handled; startup waits have a bounded retry-resistant
  deadline. See ../protocol_map/TABLE_MANAGEMENT.md and TABLE_NOTICES.md.

- Encrypted game and profile services with authenticated account binding,
  strict original requests, durable inventory and empty Viewer mailbox
  enumeration including its required checksum trailer.
- The original 30-NPC computer menu; two/three-human tables; original battle,
  ties, combos, growth, AI and special effects; original menu-generated boards
  and explicit 16/25-cell boards including opt-in CBC rewards.
- Owned-card selection, Ready/Continue/rematches, result edits, card prizes,
  optional wagers/double-up, optional offline-ring local ratings, atomic
  persistence and Returned Card recovery of unaccepted earned cards.
- Opt-in practice departures with replacement CPU, invalid and loss rules;
  retained account/card provenance and original partial-result handling.
  Economic/rated departures remain abort/refund pending stronger evidence.
- Shop purchases/sales, configured special packs, all recovered options and
  saved table defaults; paired trade; auctions, Prize Center, rankings,
  character profiles, public/private chat and room updates.
- Configured event rooms, borrowed hands, complete two/three-player event
  rounds, durable results and optional progress, F-card collection and mode8
  nonplaying admission. Event observers receive public gameplay through
  independent queues without participating in match barriers.
  Multiple configured tables within one event can match separate groups;
  event-local driver errors preserve ordinary games. Shared storage failure
  still closes all sessions, and room/event cleanup avoids circular waits.
  Fresh groups can reuse a table while another group plays; observers remain
  scoped to their watched table when a neighboring group aborts.
- Multiple configured ordinary rooms and multiple tables per room with independent
  matches, timers, departures and observers, sharing one durable account/economy
  store. Tables within a room share a single roster and replay sequence.
  Successful room moves clear the old ordinary/event roster and transient
  admission; invalid/full destinations preserve the existing membership.
- Result-screen ranking privacy saves before Continue acknowledgement and drives
  the original ranking-name display flag on new query files.
- Opt-in ordinary two/three-player observers, including late departure state
  and inactive-seat turn skips. The configurable local pool supports2..128
  accounts for simultaneous groups and observers; the default remains two.

Rule comparisons include 11,772 original battle/growth/RNG executions,
3,992 special-rule/AI comparisons, 2,118 three-player substitute AI
comparisons and 500 original prize-order/compaction comparisons. Additional
original-code fixtures cover protocol fields and lifecycle behavior; the
current artifact counts and source hashes are in ../protocol_map/AUDIT.md.
The external analysis helper emits an unclosed read-only image-handle warning;
its source repository remains unchanged. Legacy cipher work can also trigger
asyncio slow-task diagnostics.

## Native evidence and boundaries

The user previously confirmed cursor alignment and a complete CPU match.
Archived traffic in native_runs/20260926_user_cpu_complete shows admission,
selection, ten placements, a 4–6 result, Ready/Continue and return to the room.
That run predates much of the newer server work. It is not fresh native proof
for later features. Native shop entry showed 10,000 T; no purchase is claimed.

No desktop control, game launch or user-input automation is used for current
validation. The browser port remains paused. Native checks and unknown
historical policy are recorded in DEFERRED_REVIEW.md. Configured ordinary and
event rooms can host their own tables. Ordinary room tables are prepublished;
historical dynamic allocation policy remains unknown. Observer dialog timing has no recovered wire ACK.
Historical online rating/reward/penalty/schedule policies are not established
by the client alone. None of these test counts implies 100% emulation.

The separately authorized local repository
C:/Users/sruon/Documents/GitHub/tmloader contains the loader refactor.
It builds and passes its four tests; native parity remains a user check.
Nothing was pushed upstream. Existing source repositories and installed game
files remain unchanged by this server work.
