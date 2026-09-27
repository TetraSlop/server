# Packaging and transaction validation

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
