# Developer notes

[Local play](LOCAL_PLAY.md) connects the two GitHub projects. The
[native checklist](NATIVE_TEST_CHECKLIST.md) tracks the remaining user-run checks.

Start with [architecture](ARCHITECTURE.md) for feature-package navigation, code
ownership, test discovery and transaction ordering, [database setup](DATABASE.md) for persistence, and
[load testing](LOAD_TESTING.md) for repeatable measurements.
The optional [weekly rankings](WEEKLY_RANKINGS.md) record future local match
rewards and prior-week ratings using an explicit UTC calendar policy.
[Offline administration](ADMIN.md) provides account and reward inspection, prize
awards, event grants, explicit progress and eligibility through existing transactions.

[Persistence design](PERSISTENCE_DESIGN.md) explains the current data model.
[Historical research notes](research/README.md) retain the original protocol
investigation. Their bare module names, scratch paths, test commands and launcher
instructions predate packaging or the feature-directory move; use the
[root README](../README.md) for current commands and module paths. Historical
validation counts and paths describe their recorded revision, not the current tree.
Recovered rules are distinguished from local policy in
[rules provenance](research/RULES_PROVENANCE.md) and
[coverage](research/EMULATION_COVERAGE.md).

## Current iteration

See [WORK_LOG.md](WORK_LOG.md) for active work, completed fixes and deferred review.
