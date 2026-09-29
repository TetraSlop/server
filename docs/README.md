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
The optional [admin panel](ADMIN_PANEL_PLAN.md) provides a server-owned web
interface over shared queries and explicitly enabled, audited Prize Center awards
and event grants. The [original rules guide](ORIGINAL_RULES_PROTOTYPE.md) describes
the optional Win32 DLL bridge, supported routines and remaining Python ownership.
The [authentication prototype](AUTHENTICATION.md) and
[loader specification](LOADER_AUTH_SPEC.md) describe hosted sign-in and the
account-bound game/profile WebSocket gateway.

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

The [roadmap](ROADMAP.md) prioritizes the latest multi-agent code review's
confirmed defects, validation gaps and bounded readability improvements.
See [WORK_LOG.md](WORK_LOG.md) for active work, completed fixes and deferred review.
