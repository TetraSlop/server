# Server iteration log

## Current scope

Continue the approved server polish and missing-feature work in this repository.
Prioritize concrete protocol, gameplay, progression and persistence gaps, with
readable Python and regression evidence. Historical hosted reward formulas,
mission rules and schedules remain unknown unless supported by original-client
evidence; local policy must be labeled explicitly.

Do not launch/control the native game or desktop, edit original assets or live
saves, push upstream, add backups or pursue performance work. Use temporary
fixtures for tests. Record decisions needing user input or native observation
for later review and continue independent work.

## Completed and checked — 2026-09-27

- IRC channel rosters: joining a room with the supported 128-account pool
  exceeded the 512-byte framed-line limit. JOIN now emits multiple complete
  NAMES replies followed by its existing completion reply. Repeated JOIN at
  the eight-channel limit is also accepted for an already joined channel.
  Both failures were reproduced before the fix. Four new tests pass, including
  an authenticated encrypted TCP join against a 128-member roster. Existing
  transport/chat tests also pass (23 cases before the final TCP case was added).
- Generic character profiles: valid 16-byte native account names broke PG/CR/GR.
  Only the default generic display name is now bounded to its 15-byte field;
  native account/save bytes stay intact. Saved overrides no longer evaluate an
  unused default. The combined channel/transport/chat/profile regression passes
  38 tests, including encrypted reads/registration and restart.
- Ordinary table timeout/disconnect race: a queued final departure could lose
  its epoch while a timeout reset left room metadata occupied. Generation-checked
  cleanup now releases reservations, clears table/NPC metadata and publishes
  observer updates. Failed combat continuation uses the same cleanup. All three
  new regressions fail against baseline; 85 relevant recovery/room tests pass.
- Prize Center duplicate Quit: the service retained a completed receipt but
  routing sent retries to the ordinary shop and disconnected the client.
  Only an exact idle completed receipt now returns the repeated acknowledgement.
  Active shop/auction visits keep precedence; disconnect clears stale receipts.
  All 51 prize/auction/transaction/commerce tests pass, including encrypted
  retries, unchanged durable state, service collisions and stale reconnects.
- Local-history rankings: the offline ring's first winner198 incorrectly ranked
  behind loser201. Enabled local ratings now sort ascending while prize money
  remains descending. Current ratings require a committed matching ring/profile;
  fresh zero ratings, quote-only enrollment and stale rings stay unranked.
  Preserve mode retains its previous behavior. Five new policy tests and full
  encrypted two/three-player rematch/restart coverage pass in a 36-test batch.
- Generic profile read boundaries: reads, rejected edits and identical retries
  no longer create an unchanged storage write. Changed edits retain save-before-
  publication behavior. Both new regressions fail before the guard; the relevant
  profile/storage-failure batch passes18 tests.
- Trade consent: successful admission now consumes every outstanding invitation
  or approval touching either participant. Unrelated pairs and rejected entries
  retain their state. A complete encrypted three-player regression reproduces
  stale consent after a real card exchange and requires fresh approval. All40
  trade/router/commerce tests pass.
- Event spectator re-entry: ExitCh/PART retires the old sender, and delayed
  completion/error cannot acknowledge, remove or close its replacement.
  Retired writers remain tracked until shutdown. Five new regressions cover
  re-entry and shutdown; 58 broader checks and all13 final event checks pass.
- Packaging: built the wheel in an isolated build environment and verified a
  dependency-free clean installation, both entry points, runtime resources and
  both listeners on temporary ports. No native game was launched.
- Ordinary spectator re-entry: sender ownership now protects replacement
  admissions from delayed bootstrap acknowledgements and late write errors.
  Five new tests reproduce and cover the issue; all58 relevant tests pass.
- Chat: full16-byte canonical names now enter and relay successfully. Only the
  authenticated display is shortened to15; full native accounts, snapshots and
  save bytes remain unchanged. Encrypted fresh/restored/reconnect routes and a
  maximum128-account roster pass in a36-test batch.
- Event results: a trusted API iterator was consumed before participant checks,
  recording a receipt without progress. One materialized row now drives all
  validation and changes. Generator/restart/replay and invalid-final-participant
  regressions pass in a46-test batch. Current network callers already use tuples.
- Protocol documentation: preserved15 archived original envelope cases and
  clarified why arbitrary447-byte slicing cannot substitute for application
  paging. No new native execution is claimed for these archived fixtures.
- Optional weekly rankings: `--ratings local-history --weekly-rankings` on both
  server and Play records future ordinary result rewards and previous-week
  ratings with Monday UTC rollover. No backfill, rookie inference or awards.
  Periods share the existing durable rating/result transaction; disabled runs
  preserve v2 data. All40 combined state/policy/projection/encrypted/startup/Play
  tests pass in29.6 seconds. New v2 snapshots also pass the database import
  validator in memory. Real PostgreSQL execution has not been repeated here.
- Auction registration: seller/bidder history recorded at guild0 now remains
  reloadable after ordinary Init80 registration. Restore permits only unchanged
  guild or the supported0-to-registered transition; historical rows are never
  rewritten and immutable identity checks remain. Three new regressions and
  the64-test auction/profile/claim batch pass, including encrypted restart.
- Unsupported multiplayer Quit/Break: survivors no longer remain connected to
  a finished engine with no timeout. Existing durable refund/recovery completes,
  affected sessions close outside the match lock, and the old table resets.
  Generation/admission guards preserve newer tables, event observers/players and
  busy services. Event and shop rebind races both failed without these guards.
  The59-test focused departure/rooms/watchdog/prize/rules batch passes.
- Event channel PART: leaving an old event channel no longer cancels current
  event presence, selection, match or observer admission. Current-room PART and
  full socket close retain proper cleanup. Five new encrypted tests and31
  surrounding tests pass; four new regressions fail against the old handler.
- Fresh wheel build/check succeeds with80 entries: clean dependency-free install,
  module/console entry points and both listeners on temporary ports. Output is
  in results/overnight-weekly-wheel-{build,check}.log. This wheel includes weekly
  rankings and the auction/Quit/PART fixes; no native process was launched.

Whole portable regression checkpoint completed:903 tests in798.6 seconds,
846 passed,56 optional PostgreSQL tests skipped, and one login timeout in the
test client's three-second hang guard. The same test passed alone. The guard is
now ten seconds;34 affected event/server/prize tests pass after that test-only
change. Production deadlines are unchanged. Repository-wide Ruff and whitespace
checks pass. No native visual validation is claimed by these tests.

## In progress

- Whole portable suite is running against frozen source at commit db62eeb.
  Root exec session59735, output results/overnight-complete-suite.log. Database
  environment variables are cleared only for this process; external PostgreSQL
  tests will skip. Do not edit source/tests while this checkpoint runs.
- All agents' fixes are committed and all three agents are idle. Weekly focused
  output is results/weekly-ranking-checkpoint.log. Launcher tests substitute
  headless peers or mocks for every child; no native game launch is authorized.
- Read-only follow-up found one additional candidate, pending reproduction
  after this run: irc_session.py table-channel PART still calls global event
  disconnect for any configured event table. ExitCh of observer tableA leaves
  channelA joined; after entering/watchingB, a late PART A could clear B.
  Agent texture_upscaling is designing an exact table-ownership guard read-only.
  Do not edit during the running checkpoint. The other journal restore audit
  found no analogous historical-field mismatch after the guild fix.
- Prior full-suite output:results/overnight-suite.log; affected34-test rerun:
  results/overnight-timeout-recheck.log. Both runs completed. No active test
  sessions from that checkpoint remain.

## Weekly ranking design record

Optional weekly rankings use explicit local policy, requiring
local-history ratings. Use Monday00:00 UTC weeks and future recorded play only.
RR1 sums committed `(new reward_sum - old reward_sum) & 0xffffffff`; this excludes
stake refunds and unrelated shop/auction money. RR3 freezes the preceding
week's last recorded ratings. Leave RR4 empty without proven rookie inputs;
no automatic awards or reconstructed past weeks.

Compose a small RankingPeriods value inside RatingPolicy. Existing v1 snapshots
remain exact when no periods exist; initialized weekly state uses strict v2
`{version, history, periods}`. Disabled runs preserve v2 without projecting or
updating it. No new top-level journal or PostgreSQL schema migration is needed.
Old binaries will reject v2; document that deliberate format upgrade.

Record inside RatingPolicy.commit_growth using validated existing quotes and
old/new PracticeStats, preparing all state before economy commit and publishing
only after success. Reuse existing quote receipts/replay protection, avoiding
another lifetime receipt ledger. Existing MatchState staging/persistence can
remain unchanged if period changes replace owned values. A live provider keeps
RankingService projections current without changing issued immutable files.

Advance weeks transactionally before startup/ranking query/metadata reads and
within result commits. No timer is needed. Skipped weeks produce an empty
immediately preceding week; backward-period writes must not rewrite history.
Test v1/v2 restore/import, disabled preservation, boundaries/skipped weeks,
replay after rollover/restart, invalid final participants, failures before save,
refunds/wrap, CPU/human results, caps/ties and immutable files. Implementation
and the40-test combined focused check are complete. Broader validation remains.

Continue by reviewing active fixes, running their relevant checks, updating this
log and committing locally. The thread heartbeat may resume this work; it is
limited to the current allowance ending at reset timestamp1791063720, with no
reset-credit redemption or automatic continuation into a replenished allowance.

## Review later

- Native room/table/claim UI validation remains in NATIVE_TEST_CHECKLIST.md.
- Historical online rewards, event awards and economic departure penalties
  remain separate from the implemented, explicitly configured local policies.
