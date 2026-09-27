# Packaging and transaction validation

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
