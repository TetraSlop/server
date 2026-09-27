# Server iteration log

## Current scope

Continue the approved server polish and missing-feature work in this repository.
The user's latest instruction is to keep working without checking credits.
Do not query usage limits or use a displayed percentage as a stopping condition.
Do not redeem reset credits or purchase credits. This supersedes the earlier
allowance-based stopping notes in this log.
Prioritize concrete protocol, gameplay, progression and persistence gaps, with
readable Python and regression evidence. Historical hosted reward formulas,
mission rules and schedules remain unknown unless supported by original-client
evidence; local policy must be labeled explicitly.

Do not launch/control the native game or desktop, edit original assets or live
saves, push upstream, add backups or pursue performance work. Use temporary
fixtures for tests. Record decisions needing user input or native observation
for later review and continue independent work.

## Completed and checked — 2026-09-27

- IRC channel capitalization no longer creates duplicate memberships or misses
  departure cleanup (`f9e0235`). Known rooms/tables/services retain their exact
  advertised spelling; generic channels reuse their first spelling. ASCII
  letter folding is local compatibility policy, with punctuation kept distinct.
  Three encrypted regressions fail before and pass after, including event
  selection release and ordinary observer admission/exit. Fifteen existing
  controls and nine advertised-channel spelling checks pass.
- Room DE/PD now authenticates the full canonical character name before
  publishing roster changes (`327c56f`), alongside nickname/character identity.
  Four forged-name subcases fail before the fix; two new encrypted tests pass
  after, including names differing only in their sixteenth byte and complete
  server snapshot/delta/save checks. Forty-one existing controls and the
  unchanged captured native startup replay pass. Source research `624bdba`
  passes 23 original cases proving CN is separate from the handle HN. It also
  exposes an existing native 16-byte name terminator/metadata limitation,
  recorded in research/ROOM_NAME_PROVENANCE.md and DEFERRED_REVIEW.md; synthetic
  16-byte server tests do not establish native sender/display safety. Collection
  finds 1,135 tests without import errors; Ruff/whitespace pass. The rebuilt
  wheel passes clean installation, entry points, administration/recovery and
  temporary listeners, with all 80 runtime files byte-matching source.
- Event table completion, cancellation and explicit observer ExitCh now detach
  exact table chat membership before reuse (`26e162c`). The coordinator emits
  a transient release fact consumed under the adapter lock; roster notices use
  the existing ordered queue. Room/destination chat and healthy quitting peers
  remain intact. Finished observer previews stay until explicit exit. Three
  encrypted regressions fail before and pass after; 11 existing controls and
  four release-source scenarios pass. Root review found no additional issue.
- Original event-entry denial research (`8111a26`) passes eight instruction
  cases and proves automatic selected-room PC before the rejection notice.
  Three encrypted expired/excluded/post-end cases preserve durable state and
  clean on PC; transient room presence awaiting that cleanup is compatible, so
  no denial-path fix was made. Thirteen admission/selection boundary controls
  pass. See research/EVENT_ENTRY_DENIAL.md and VALIDATION.md. Collection finds
  1,130 tests without import errors; Ruff/whitespace pass. The rebuilt wheel
  passes clean installation, entry points, administration/recovery and temporary
  listeners, with all 80 runtime files byte-matching source.
- Native room departures now detach chat membership as well as the room roster
  (`0a2ec6a`), including ordinary/event room changes and PC exits. Delayed exits
  for an old room preserve destination chat. A separate table fix (`a34cece`)
  clears the exact table's chat membership on GameQT, GameExit and host kick,
  preserving other groups, remaining reservations and healthy connections.
  Six room and three table subcases failed before their fixes; the respective
  focused selections pass all 13 and 12 tests. Notifications run after the
  synchronous detach, outside the table lock. No native launch was used.
- Valid 16-byte canonical account names now enter ordinary and event matches.
  Registration validates the full name before storing a 15-byte client label;
  event membership uses the same bounded presentation. Authentication and
  account/profile persistence retain all 16 bytes. Five new regressions pass,
  including complete encrypted ordinary/event games, progress and restoration;
  102 existing admission, match and event controls also pass. Research
  `9f77341` passes 34 original-consumer checks with deliberately identical
  displayed prefixes, confirming distinct IDs, local seats, owner flags and
  event progress. See research/DISPLAY_NAME_IDENTITY.md and VALIDATION.md.
  Collection finds 1,127 cases without import errors; Ruff/whitespace pass.
  The rebuilt wheel passes clean installation, entry points, administration,
  recovery and temporary listeners; all 80 runtime files byte-match source.
- EventResults now prepares all progress updates before publishing any member
  changes. A final participant's conflicting existing progress receipt could
  previously leave earlier participants advanced in a direct service call,
  despite no completed result receipt. The existing network transaction already
  protected durable state. The new regression fails before the fix and checks
  unchanged accounts/journals plus valid retry and exact restart afterward.
  Twenty focused result/transaction checks pass in 0.073 seconds. Independent
  review found no regression. A separate event claim/membership audit passes
  30 existing tests and six combined partial-claim/cap/exclusion/restart cases,
  finding no additional defect; no duplicate tests were added. See VALIDATION.md
  and research/EVENT_ADAPTER_NOTES.md.
  Three encrypted round/progress/save-failure tests pass in 13.286 seconds.
  Collection finds 1,119 cases; Ruff/whitespace pass. The rebuilt 85-entry wheel
  passes clean installation, entry points, administration/recovery and temporary
  listeners, with all 80 runtime files byte-matching source.
- VS. Rating source research (`ade40fb`) recovers the original offline random
  component generator and its separate presentation rule: first result order
  draws two rand%1000 components; other orders draw -(rand%1500),0. The original
  formatter clears the second component for result classes at least two.
  This can treat fully tied seats asymmetrically and is not historical hosted
  policy. All 93 original checks pass, including complete hosted profile import,
  ResultData receive, presentation and export at unsigned RT boundaries. Neutral
  T0/T1 preserves every imported bit, so the current server needs no runtime
  fix. Evidence is in research/VS_RATING_SOURCES.md; optional adoption is recorded
  for later review. Existing average-rating policy remains separate.
- Auction follow-up found no query, settlement or recovery defect. All 12 wire
  service checks pass in 0.037 seconds. An independent 36-test settlement and
  transaction selection passes in 4.238 seconds; 13 additional in-memory
  scenarios preserve combined payment/refund cap residuals across three
  restarts and exercise expiry during each collection phase followed by
  completion, disconnect or restart. Frozen collection contents, neighboring
  reservations, later receipts and total cards/money remain correct. Existing
  regressions cover these paths, so no duplicate tests or runtime edits were
  added. Exact selections/results are retained in the auction audit logs named
  in VALIDATION.md.
- Memorable Win source research (`addf270`) passes 458 original-instruction
  cases with a write watch over RN: all 430 ordinary offline score/seat
  combinations and ten online result controls preserve it; three profile
  imports and 15 rotated ResultData controls identify its actual writers.
  Missing local RN preserves, empty clears and supplied text replaces; another
  seat's text cannot overwrite the local value. No offline selection formula
  or runtime defect was found; historical hosted selection remains unknown.
  See research/MEMORABLE_WIN_SOURCES.md for exact execution scope and code hash.
  Separately, 22 weekly period/policy/projection tests pass in 0.502 seconds
  with no progression defect found. Updated deferred native review instructions
  to the packaged launcher and example paths; Play remains explicitly JSON.
  Runtime is unchanged, so the previously checked wheel remains current.
- Event configuration now rejects boards with insufficient placement space
  before publishing tables or reserving players. Previously JSON accepted such
  boards and matchmaking disconnected the selected players when the engine
  rejected them. Shared native tile classification preserves placeable F3/4/5
  effects; dimensions, non-CBC tiles and unsigned seeds are validated centrally
  in EventTableConfig, which also freezes caller-supplied boards. Four new
  regressions reproduce 25 failing subcases before the fix, then pass alongside
  all 33 configuration/round/coordinator checks. Exact-capacity two-human,
  three-human and synthetic CPU rounds complete. Independent review found no
  further constraint mismatch. A separate cancellation audit passed 23 existing
  tests and 14 in-memory phase combinations without finding stranded tokens or
  cross-table interference; no duplicate tests were added. See VALIDATION.md
  and research/EVENT_CONFIGURATION.md. No new historical policy is claimed.
  Collection finds 1,118 cases with no import errors; Ruff/whitespace pass.
  The rebuilt 85-entry wheel passes clean install, entry-point, administration,
  recovery and temporary listener checks; all 80 runtime files match source.
- Rematch follow-up found no atomicity or stranded-player defect. Two new
  encrypted regressions (`909bef5`) cover four complete rounds: a prize loser
  left with four cards, and a player unable to fund a doubled stake, each in
  both voting orders. Rejected final votes preserve engine/RNG, previous votes,
  pending rewards, reservations, room and saved state; connection cleanup
  releases both peers and preserves every earned card through exact restart.
  The tests pass in 11.913 seconds. Original execution (`45401b4`) proves normal
  yes emits no packet when cards/money are insufficient, while Computer Conf1
  deliberately permits insufficient money to choose another setup. Fourteen
  original eligibility cases plus explicit leave pass; no new error policy.
  Updated the older underfunded-start regression to raise the wager after both
  peers join, since the new admission guard correctly refuses it earlier.
  All 21 related prize/wager/privacy checks pass in 53.398 seconds. Collection
  finds 1,114 tests; Ruff/whitespace pass. No runtime changes or wheel rebuild;
  all 80 packaged runtime files still byte-match source. See VALIDATION.md and
  research/CONTINUE_ELIGIBILITY_NOTES.md.
- New ordinary admissions now enforce the original menu's inventory, CP and
  money restrictions against committed accounts before claiming a seat. The
  previous adapter checked only capacity. Five-card minimum applies to human
  and Computer entry; configured human tables additionally check inclusive CP
  bounds and wager affordability. Empty custom/fixed tables, Computer entry,
  existing members and observers retain their distinct native paths. Rejected
  GameENC no longer replaces the empty match generation before validation.
  Nine in-memory subcases and an encrypted rejection/retry regression reproduce
  the old gap. All 45 distinct focused checks pass, including cardless observers
  and unchanged account/table/save state on rejection. Original fixture
  `018d201` preserves 31 passing instruction cases; encrypted test `1dd3361`
  verifies the same connection joins at the exact CP boundary after correction.
  Error-code selection and direct Req enforcement remain explicit local policy.
  Collection finds 1,112 tests with no import errors; Ruff/whitespace pass. The
  rebuilt 85-entry wheel passes clean dependency-free install, entry points,
  administration/recovery and temporary service listeners. See VALIDATION.md
  and research/TABLE_ELIGIBILITY_NOTES.md. No native game or live save touched.
- Bounded trade-entry audit found no production consent bypass or stranded
  reservation. Router grants are rechecked before every service entry; explicit
  cancellation and disconnect prevent retained internal authorization from
  being reused. All 26 existing router/service tests, 12 malformed in-memory
  variants and two disposable encrypted cancellation/reconnect scenarios pass.
  No speculative change was made to the documented failed-entry retry policy.
- Original Rule/CheckJoin audit found no demonstrated defect in current table
  ownership checks. Rule uses the admitted table identity; all three nonzero
  CheckJoin flag combinations select the same recovery cleanup and emit ExitCh.
  The individual flags' historical server meanings remain unproved, so existing
  availability mappings remain explicit local policy. Both Rule event branches,
  response/cache export and four flag probes pass. Reproducible evidence is in
  research/TABLE_QUERY_NOTES.md (`04fbb4d`); no runtime changes were made.
- Portable migration audit restored the standalone NPC selector/name/guild
  API check inadvertently excluded with its native-dependent class, and added
  direct name-visibility boundary checks. Removed three empty migration test
  shells; original instruction tests remain archived. All six NPC API/batch
  tests pass in 0.017 seconds; collection finds 1,106 tests without import
  errors. Ruff and whitespace checks pass. No runtime change or wheel rebuild.
- Follow-up original Computer-menu execution exposed incompatible optional
  fixed CPU presets: hosted constructor100B9DA1 sets gs0, ComGameInit has no
  override, and the menu offers no departure-rule control. Room configuration
  now rejects fixed gs1/2 if any primary/additional table is CPU, before startup
  can publish an unplayable preset. Generic engine message domains remain
  unchanged; shipped Local Room and human presets are unchanged. Two new tests
  reproduce the missing validation before the fix. All 20 relevant checks,
  ten mocked-launcher/configuration checks, and the final three new cases pass;
  the latter include actual original-start bytes over encrypted transport into
  card selection/StartData with saved preferences preserved. Original evidence
  `52611e3` also proves optional Computer editing remains unlocked and clears
  double-up; direct starts preserve it. The limitation is documented for later
  native review. The rebuilt 85-entry wheel passes clean installation and
  temporary service/admin checks, with all 80 runtime files equal to source.
  See VALIDATION.md and research/COMPUTER_ROOM_RULES.md. No game/assets/live
  save/desktop input was touched.
- Room follow-up found no new production defect. Original admission/editor/
  sender execution confirms that fixed rooms skip the game-rule editor and
  copy the advertised PTL game rules, while Open Tables uses saved game defaults.
  All four captured Apply packets pass encrypted replay against the exact
  production directory/table fixture hashes. Another encrypted test moves an
  account through every human room and back, then reloads its temporary save:
  personal defaults and presets remain independent. The new lifecycle checks
  (`a18b933`) cover capped double-up, three completed CPU rounds, rematches,
  opponent-count reconfiguration, atomic rejection of conflicting settings and
  every departure rule after a human rematch. All six focused integration/
  lifecycle tests pass in 12.667 seconds; 17 wager/departure/lifecycle checks
  also pass separately. No runtime change or wheel rebuild was needed. Initial
  editor proof and remaining scope are in research/FIXED_ROOM_EDITOR.md
  (`65b64cf`). Collection finds 1,101 tests without import errors; Ruff passes.
- Latest user-reported room fix: Computer was greyed out because RL record
  `+0x29` was zero. Original instruction execution confirms this flag is
  independent of fixed-rule display at `+0x28` and Chat at `+0x70`. Corrected
  Local Room enables Computer with zero money; human tables advertise prefix
  `B` and reject direct Computer admission without reserving or disconnecting.
  The launcher now adds Classic, Special and Quick Play fixed rooms alongside
  Open Tables. A validated immutable RoomRules value supplies advertisements,
  engine rules and resets; saved preferences do not overwrite the preset.
  Configuration/enforcement is explicit local policy. Foundation `e247d04`,
  original evidence `79f34a5`, encrypted fixed-room startup `b6c4547`.
  All 131 engine, 41 adapter, 31 admission, six discovery and two fixed-room
  focused test executions pass; original probes also cover all five actual
  production advertisements. Review found no actionable issue. Collection is
  1,097 tests; the full suite and real PostgreSQL were not rerun. The new
  85-entry wheel passes clean installation and temporary service/admin checks;
  all 80 runtime files match source. Details and hash are in VALIDATION.md.
  User should restart Play.cmd, enter Local Room, select an empty table and
  choose Computer. Native menu rendering still needs user observation.
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
- Resumed work adds `tmserver-admin account --account-key KEY`: saved balances,
  card count, Prize Center points/pending award, event membership and remaining
  claims. Tests use a real partial event collection with a capped balance, check
  exact account ownership, preserve null journals and pending recovery, and
  prove the inspection leaves the save byte-identical. Commit 861ef3d.
- `tmserver-admin event-progress` records explicit operator steps and configured
  mission bits through the existing event transaction and durable replay IDs.
  Exact retries do not repeat progress; conflicting IDs, invalid facts, missing
  or excluded members fail without changes. Save failure leaves the ID retryable.
  It grants no assets and infers no historical triggers. Commit d618485.
- The follow-up admin/event/prize batch passes 54 tests in 2.178 seconds. The
  rebuilt 82-entry wheel passes clean stdlib-only installation, actual JSON
  award and account-inspection commands, entry points and both listeners.
  Logs: results/event-progress-wheel-{build,check}.log. Collection finds 992
  cases without import errors; the full 992-case suite was not repeated.

Earlier portable regression run:903 tests in798.6 seconds,
846 passed,56 optional PostgreSQL tests skipped, and one login timeout in the
test client's three-second hang guard. The same test passed alone. The guard is
now ten seconds;34 affected event/server/prize tests pass after that test-only
change. Production deadlines are unchanged. Repository-wide Ruff and whitespace
checks pass. No native visual validation is claimed by these tests.

## Resumed server work — 2026-09-27

- Ordinary table rules freeze when the start handshake begins. Exact current
  settings retries remain no-ops; saved defaults and lobby edits before Start
  still work. This prevents peers accepting different rules during one startup.
  Commit 36734cf passes 141 scoped tests including captured startup replay.
- Event ready messages now enforce event end and the shared admission deadline
  before starting a match. Existing games and post-event reward claims continue.
  Commit 4de3c34 passes 62 scoped tests, including both expiry boundaries.
- Added read-only `tmserver-admin recovery`: restored copies run the actual
  startup recovery rules and report affected balances and Returned Cards.
  Six new tests compare forecasts with headless startup, prove repeated previews
  preserve saved bytes, and distinguish settled rewards and auction refunds.
  The 93-test recovery/admin/economy batch passes in 2.717 seconds.
- Collection finds 1,010 cases without import errors. The rebuilt 83-entry wheel
  passes clean stdlib-only installation, real JSON award/account/recovery
  commands and both service listeners. Preview leaves the disposable save
  byte-identical. Logs: results/recovery-preview-wheel-{build,check}.log.
  The complete suite has not been repeated after these scoped changes.

## Earlier recovery and admission checkpoint

- Ordinary PART/disconnect publication no longer closes survivors who have
  entered another table, event or shop while cleanup awaits delivery. Shared
  ownership guards cover both callers and the earlier Quit/Break path. Failed
  cleanup still closes peers retaining the old reservation. Commit 5ee9e81
  passes 68 focused checks, including 13 new rebind/failure scenarios.
- Late B2 event selection now uses the same expiry rules as polling and ready
  messages. Expired Decks/CardSelect/Quit cannot save an edit, return success or
  create a delayed-editor receipt; active games and legitimate old receipts
  retain their behavior. Commit ec290e0 passes 33 domain and three encrypted
  lifecycle/failure checks, including eight new regressions.
- A claim-delivery audit passed 92 domain/encrypted tests without finding a
  new defect. Transmission failure at individual entry responses is the next
  focused check; results/collection-delivery-audit.log records the baseline.
- Follow-up collection checks now interrupt every response of Prize Center,
  event-card and multi-page auction entry. All eight cutoffs preserve committed
  credits and all uncollected cards, release the failed peer's reservation, and
  leave another peer's active Prize Center session intact. Commit f8cc389 adds
  three parameterized tests; the 31-test integration/transaction batch passes
  in 33.442 seconds. No production change was needed.
- Journal restoration now rejects nine previously lossy extension locations,
  including nested award and reward receipts. Commit cab0f83 covers auction,
  Prize Center, result rewards and character profiles. Seven new regressions
  prove rejection through direct restore, shared validation, admin and startup
  before any save rewrite; 128 related checks pass. The legacy optional auction
  return journal and absent/null service journals remain supported.
- The isolated complete run of 5ee9e81 finished 1,030 cases in 1,181.037 seconds:
  969 passed, 59 optional PostgreSQL cases skipped, two spectator-rule setup
  failures. The helper sent Apply after Req began the frozen start handshake.
  Captured commands 6/15/27/29/30 establish GameEN, Apply, GameReady, Req, GameOK.
  Test-only commit 46d6de5 uses that order and verifies PLAYACK's observation rule.
  Both failed cases and all 65 broader observer/startup/capture checks pass in
  125.977 seconds. Production protection remains intact. All 971 non-database
  cases from that complete run passed across the run and targeted correction;
  the entire run was not repeated afterward.
  The temporary source archive was removed. Logs:
  results/resumed-complete-suite.log and results/resumed-complete-suite.json.
  No live save or database was used.
- Diagnostic I/O failure no longer interrupts protocol replies or cleanup.
  The previous log flush failure stopped shutdown after one peer, left a
  healthy survivor reserved, and could retain a completed accept task. Commit
  8b17927 keeps file ownership/JSONL contents, reports once through standard
  logging and disables later diagnostic writes. Save failures remain fatal.
  Eight new tests plus nearby server/profile/recovery/transaction checks pass
  all 79 cases. Game startup still rejects an unopenable configured log path.
- Startup field validation now shares the same boundary as import/admin.
  A disposable interrupted-wager save reproduced silent loss of an unknown
  journal and account field during automatic recovery. Startup now rejects
  these fields before recovery/writes, for JSON and injected stores. The local
  setup check validates all service journals while preserving pending recovery.
  Six new tests cover these cases and older absent/null journals. In total,
  77 distinct startup/Play/lease/admin/JSON/recovery tests passed across two
  runs. The first run also named a nonexistent module; the corrected recovery
  run passed 14 cases (six already covered plus eight session-recovery checks).
  Logs: results/startup-validation-tests.log and
  results/startup-validation-recovery-tests.log. Ruff passes.

- Temporary PostgreSQL and isolated-source cleanup are complete. The resumed
  complete suite covered 1,030 cases; later journal/logging changes and the
  spectator setup correction pass scoped checks, as recorded above. Current
  collection includes 18 additional tests added after the isolated checkpoint.
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
- The earlier complete passing run contains 988 cases; the resumed complete
  run exercised 1,030, with its two setup failures corrected and verified above.
  Current collection contains 1,048 cases, including 59 optional PostgreSQL cases.
  No known failures or pending fixes remain at this checkpoint. Optional real
  PostgreSQL checks were not repeated after the earlier isolated 59-case pass.
- The user explicitly resumed work, requested no further credit checks and
  reported resetting access themselves. Continue the approved work without
  querying usage or stopping at a displayed percentage. Do not purchase credits
  or redeem a reset through tools.
- Recurring continuation is active with the updated instruction. Review and
  validation for this checkpoint are complete. The clean 84-entry wheel passes all
  dependency-free install, entry-point, JSON admin/recovery and listener checks;
  all 79 packaged runtime files byte-match the current source. Logs:
  results/resumed-final-wheel-{build,check}.log.
  `texture_upscaling` completed journal field preservation;
  `collection_delivery_audit` completed individual response interruption tests;
  `browser_port_options` completed diagnostic failure handling and corrected
  the spectator setup against original startup ordering. Agents are idle and
  no exec sessions remain active.
  Changes require concrete failures and must preserve existing protocol policy.
- Prior full-suite output:results/overnight-suite.log; affected34-test rerun:
  results/overnight-timeout-recheck.log. Both runs completed. No active test
  sessions from that checkpoint remain.

## Two-NPC Computer menu and peer routing — 2026-09-27

- Original menu events, confirmation state and serializer instructions prove
  the one-human/two-NPC path: header 3, two distinct opponent IDs, 25 cells and 15
  placements. Three frozen original sender vectors are retained in
  `tests/fixtures/three_player_computer_sender.json`; the instruction addresses
  and image hash are documented in `research/NATIVE_OPPONENTS_NOTES.md`.
- The engine now keeps a separate identity, original personality, rating and
  hand for each selected NPC, regenerates both hands on rematch and supports
  switching between one and two opponents. Validation precedes stake debit,
  RNG and board changes. Mixed human/CPU prize choices continue automatically
  after a human selection and retain the durable settlement barrier. Engine
  commit `55da252` passes 136 focused gameplay/AI/prize/observer checks.
- The adapter publishes both NPCs with distinct reserved nickname IDs, removes
  the second when switching back to one opponent, and preserves other tables'
  NPCs. Dynamic admission debits the intended player count before engine resize;
  Computer tables still admit exactly one human. Commit `c6ec25e` passes 52
  surrounding tests and 18 final room/adapter checks, including failed-save
  atomicity and sibling-table isolation. No persistence schema changed.
- Five encrypted gameplay/persistence tests (`f2306f8`) pass in 8.717 seconds.
  They cover complete 15-placement games, rematches and 2↔3 reconfiguration,
  wagers/double-up, ratings, owned-card prizes, interrupted refunds and failed
  debit publication. A legal deterministic human win earns two actual cards
  before interrupted acceptance; restart returns those cards exactly once.
  Default practice preserves money and ownership. The unrelated account is
  unchanged throughout.
- An encrypted three-seat observer test (`1732f89`) covers early and mid-game
  admission, the 25-cell board, both NPC IDs, all 15 placements and ResultData/P3.
  Observers cannot submit gameplay acknowledgements or receive private result
  messages. All six surrounding observer tests pass; four original receiver
  probes accept production bytes for both name-visibility thresholds.
- Independent review found no actionable regression. All 435 distinct NPC pairs
  fit initial PLAYACK/TCList frames (maximum 398/372 bytes), all six finishing
  orders complete mixed human/CPU prizes and rematches, and encrypted selection
  cancellation refunds once, removes both NPCs and releases the account.
- A bounded CBC ownership audit found no defect in disposable three-seat
  Computer adapter fixtures. NPC-owned generated cards grant nothing to the
  human. One human-owned generated card plus two ordinary prizes recover or
  accept exactly once, with no NPC account created. The native ordinary board
  generator emits no CBC/F18, and explicit three-seat Computer boards remain
  outside supported configuration; this audit does not expose that combination
  as a new native feature.
- Original instruction research found that the local menu constructs both
  ten-card NPC pools before selecting either five-card hand. The initially
  sequential helper generated different cards. Commit `395cb24` shares a batch
  helper with the unchanged single-NPC API. Eight frozen original cases match
  all represented card fields, full RNG stream hashes/counts and final states.
  All 120 focused NPC/engine/AI/board/prize checks pass; another 120 comparisons
  against the previous single-NPC implementation are unchanged. The eight
  encrypted Computer/observer/persistence checks pass in 18.416 seconds.
  Their practice expectation now handles human wins correctly: with wagers
  disabled, payout stays zero (`dbf39f4`). No server reward policy changed.
  The composed original stages exclude intervening scene/board/frame draws;
  historical hosted scheduling is still unknown.
- Peer-relay review reproduced forged maintenance/status and initial trade
  controls. The original receiver ignores the fifth prefix selector, so blocking
  only canonical `GTM0G` was insufficient. Commit `424a741` permits only validated
  peer chat/trade within the entire `GTM0` namespace after service dispatch;
  opaque non-game traffic retains its existing behavior. Four new encrypted
  cases and 36 related tests pass, including 52 rejection subcases, alternate
  selectors, lower-case hex, recipient isolation and legitimate traffic.
  Original ingress/receiver evidence is in `research/PROTOCOL_NOTES.md`.
- Removed the unreachable fixed-account CR registration fallback (`2f0155c`);
  authenticated character registration already owns that path. Sixteen scoped
  checks pass, with another 20 profile/registration/capture checks independently
  passing. The change removes 20 lines and has no intended behavior change.
- Further audits found no new defects: 383 captured-request mutations caused no
  unexpected exception or stranded reservation; 189 malformed/stale match
  requests left complete engine/RNG state unchanged. The profile audit preserved
  pending reward/result/event/auction journals through account-isolated reads,
  handle changes, retries and file deletion requests. No speculative protocol
  behavior was added from these audits.
- The complete isolated suite at `f2306f8` passes 1,075 tests in 978.914 seconds:
  1,016 passed, 59 optional PostgreSQL cases skipped, and no failures. Its
  temporary source directory was removed. Output and source metadata are in
  `results/three-player-computer-complete-suite.{log,json}` and detailed checks
  are in [VALIDATION.md](VALIDATION.md). The final 84-entry wheel passes
  a clean dependency-free install, entry points, JSON admin/recovery commands
  and both temporary listeners; all 79 runtime files byte-match source. Logs:
  `results/computer-batch-wheel-{build,check}.log`. The final package includes
  `395cb24`; current collection finds 1,079 tests without import errors. The
  complete archived run predates that correction and its four fixture tests,
  which have the separate focused checks above. Native rendering and hosted
  reward equivalence remain separate from these headless checks.
- All test/package processes for this checkpoint have completed, and reviewers
  are idle. Ruff and whitespace checks pass. Changes remain local to the GitHub
  checkout; no native game, live save or upstream repository was touched.
  Continue with evidence-backed gaps; do not query credits or manufacture
  protocol policy merely to create more work.

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

Continue evidence-backed protocol and recovery investigation from this checked
source. Avoid inventing historical policies or changing supported behavior just
to add work. The thread heartbeat may resume under the latest user instruction:
keep working without checking credits.

## Review later

- Native room/table/claim UI validation remains in NATIVE_TEST_CHECKLIST.md.
- Historical online rewards, event awards and economic departure penalties
  remain separate from the implemented, explicitly configured local policies.
