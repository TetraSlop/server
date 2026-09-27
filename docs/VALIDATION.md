# Packaging and transaction validation

## Latest complete-suite checkpoint, 2026-09-27

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
