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
- Whole portable checkpoint at source commit db62eeb passed:952 tests in791.0
  seconds,896 passed and56 optional external PostgreSQL tests skipped. Output:
  results/overnight-complete-suite.log. No failures or native game launches.
- Event TABLE-channel PART now checks exact participant or observer table
  ownership. A stale leave cannot clear a newer observer, participant or
  unassigned selection. All3 new encrypted regressions fail on the old handler;
  the final47-test event batch passes. Committed as f43215a.
- JSON lifetime writer lease is shared by normal standalone/Play services and
  admin. It covers load/recovery through both shutdowns and preserves existing
  short commit/Play locks. Ten new tests and30 existing checks pass; separate
  process exclusion, canonical aliases and failures are covered. Commit5676f9c.
- Offline admin provides exact saved-account lookup, queued Prize Center awards,
  explicit event grants and eligibility. It requires a selected existing save
  or database and matching saved event configurations, runs no recovery or
  listeners, and reuses existing claim transactions. Domain snapshot validation
  was extracted so JSON remains stdlib-only; database public aliases remain.
  All97 related tests pass in21.94 seconds using the repository environment.
- Final82-entry wheel passes clean no-deps installation, actual temporary JSON
  award command and both listeners. Logs:results/overnight-admin-wheel-{build,check}.log.
  Local editable install refreshed; tmserver-admin --help works. Collection981
  tests has no import errors. A later PostgreSQL run is recorded below.
- Continue privacy: the accepted result's profile fingerprint now follows its
  own validated privacy change, preserving delayed deck edits across a rematch's
  stake refresh. An unrelated profile change still invalidates the context.
  First/final-voter encrypted tests cover ownership, stakes, deck edits, restart
  and save failure. All 42 privacy/reward/transaction tests pass. Commit 6919673.
- Accepted result state now uses immutable named fields for its seat,
  reservation, account and profile fingerprint instead of positional tuples.
  The same 42 tests pass after this readability change (77cf376). Another
  38 encrypted CPU, three-player, wager, prize and ranking tests pass against
  the privacy fix before the representation-only change.
- Real PostgreSQL 16.15 validation passes all 56 existing backend cases and
  three new offline-admin cases. The latter cover award replay after redemption,
  event grants/eligibility/configuration, preserving weekly rankings, and actual
  advisory writer exclusion. Only a new private temporary host was used.
  Commit 0280422 includes the tests and database validation report. Its owned
  container, daemon, mounts and temporary data were removed; no listener remains.
- The rebuilt final 82-entry wheel passes clean installation, the real JSON
  admin award command, entry points and both service listeners. Logs are
  results/overnight-final-wheel-{build,check}.log. No optional runtime dependency
  was needed.
- Final complete suite at fd56461: 988 tests in 842.506 seconds, with 929 passed,
  59 optional PostgreSQL cases skipped and no failures. The 59 PostgreSQL cases
  passed separately as recorded above. Log: results/overnight-final-complete-suite.log.
  The final source, including the immutable result context, is covered by this
  complete run. Launcher output mentioning game windows comes from mock tests.

Earlier portable regression run:903 tests in798.6 seconds,
846 passed,56 optional PostgreSQL tests skipped, and one login timeout in the
test client's three-second hang guard. The same test passed alone. The guard is
now ten seconds;34 affected event/server/prize tests pass after that test-only
change. Production deadlines are unchanged. Repository-wide Ruff and whitespace
checks pass. No native visual validation is claimed by these tests.

## Current checkpoint

- All assigned implementation and temporary PostgreSQL cleanup are complete.
  Agents are idle and no exec sessions remain active. The complete current
  portable suite finished successfully with TM_TEST_DATABASE_URL absent.
  Latest production commit is 77cf376; subsequent changes add tests and records.
- The transport/profile audit found no new actionable defect. Isolated encrypted
  probes rejected truncated bodies, incorrect extents and bad checksums without
  publishing state; valid follow-up requests succeeded. No protocol policy was
  changed based on speculative request ordering.
- Final admin and shop/trade audits found no new concrete defect in owner
  selection, journal preservation, cancellation, re-entry, shared B2 routing or
  delayed edits. No speculative retry policy was added.
- A final synthetic ranking audit confirms fresh hide-name settings after weekly
  rollover, immutable issued files, account-isolated reads/deletes and no files
  retained after restart. Existing file privacy policy remains unchanged.
- The latest complete run and collection both contain 988 cases, including
  59 optional PostgreSQL cases. No import errors or test failures remain.
- Before further substantial work check current allowance. Last observed 99%
  used; reset remains 1791063720. Continue only concrete, bounded improvements
  that can be completed and validated within remaining allowance. No reset
  credits, purchases or continuation into a replenished allowance are authorized.
- The recurring heartbeat is paused to avoid starting more work after this
  allowance expires. The final suite and checkpoint are complete; no reset
  credit or purchase has been used.
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
and the 40-test combined focused check are complete. Subsequent complete
portable runs and the real PostgreSQL administration checks passed, as above.

Continue by reviewing active fixes, running their relevant checks, updating this
log and committing locally. The thread heartbeat may resume this work; it is
limited to the current allowance ending at reset timestamp1791063720, with no
reset-credit redemption or automatic continuation into a replenished allowance.

## Review later

- Native room/table/claim UI validation remains in NATIVE_TEST_CHECKLIST.md.
- Historical online rewards, event awards and economic departure penalties
  remain separate from the implemented, explicitly configured local policies.
