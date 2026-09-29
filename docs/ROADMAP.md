# Server roadmap

The later authorized [authentication prototype](AUTHENTICATION.md) now verifies
Supabase identities and carries account-bound game/profile streams through an
optional gateway. Verified Discord users now receive a persistent account on
their first Play request, without manual enrollment or a fixed account pool.
Legacy pilot bindings migrate without resetting player data. The user has confirmed
real Discord sign-in through the separately owned loader and the imported display
name. An automated encrypted replay of the captured wagered COM request completes
a three-player match through the hosted tunnel, including settlement and exit;
a complete native hosted COM match still needs user acceptance.
Public deployment remains a subsequent milestone. See the
[loader contract](LOADER_AUTH_SPEC.md).

Reviewed at `9cdc374` on 2026-09-27. Three independent agents reviewed
protocol/gameplay, economy/persistence, and architecture/packaging; the main
agent checked the findings and combined the priorities below. This was a code
review with bounded synthetic reproductions, not a native-client acceptance run.
No runtime fixes were made during this sweep. Recurring automation remains paused.

Implementation was subsequently authorized. R1/R2/R4/R7 and R3 validation tooling
are implemented. R6's match/table helpers and stateless shop extraction are now
implemented with focused regression coverage. R9 has a working optional,
authenticated panel, read-only by default, with opt-in audited Prize Center awards
and event card/money grants, eligibility and progress corrections shared with
offline administration. Read-only receipt
history preserves operator facts after collection and restart.
Pydantic handles configuration and auction save shapes, and Construct owns
directory records. An opt-in native-hybrid backend now connects the original
battle, growth, target selection and ordinary AI routines to the server without an emulator.

See [VALIDATION.md](VALIDATION.md) for current test and package results. Live
PostgreSQL validation now runs against an isolated local PostgreSQL instance. R5 native
acceptance is user-operated; R8 still needs decisions or original-client evidence.

Combined, targeted and installed-package validation are recorded in the validation
report. Next complete the user-operated native acceptance checklist, then public
TLS deployment and admission controls. Keep Python, feature ownership, explicit
transaction coordinators and SQLAlchemy Core.
The current transaction contexts already express their participants and commit
order clearly; a generic transaction framework is not justified by this review.

## Review baseline (2026-09-27, before implementation)

- Runtime: 20,868 physical Python lines across 108 files, including comments and
  blanks. Tests are separate: 34,719 lines across 173 files.
- Current discovery: 1,179 unique test IDs, with no import errors.
- Latest changes passed 129 focused tests, lint and clean-wheel checks. The last
  complete suite was at an earlier revision: 1,111 passed and 59 PostgreSQL tests
  skipped. This review did not rerun that complete suite.
- The current wheel is clean and its 111 runtime files match source. The normal
  package checker nevertheless accepts extra stale modules; see R3.
- Earlier user-confirmed play is recorded separately from acceptance of the
  latest server/loader/assets combination in the native checklist.

Effort below describes scope, not elapsed-time estimates: **S** is a narrow
change with focused checks; **M** crosses a few owners or requires integration
coverage; **L** needs protocol research or a product decision.

## Recommended order

| Item | Priority | Outcome | Effort | Dependency |
| --- | --- | --- | --- | --- |
| R1 | P1 | An unsupported spectator snapshot cannot interrupt participants. | S–M | None |
| R2 | P1 | Oversized replies are rejected before state changes; room replay and profile reads remain valid. | M | One shared reply-budget contract |
| R3 | P1 | Package and test verification run reproducibly and reject misleading passes. | S + M | Can run alongside R1/R2 |
| R4 | P1 | Incompatible local rating history is reported before starting play. | S | Existing consistency rules |
| R5 | Acceptance milestone | One recorded native candidate passes the core play/reload scenarios. | User-operated | R1/R2, then R3 verification |
| R6 | Implemented | Long handlers and shop behavior have smaller, explicit responsibilities. | M per change | Regression verification |
| R7 | P2 | Offline inspection shows all assets still waiting to be claimed. | S | Existing journal read APIs |
| R8 | Deferred | Rating migration and spectator synchronization have explicit policies/protocols. | M–L | Decisions or original-client evidence |
| R9 | Implemented | An authenticated panel shares queries, Prize awards, event grants, eligibility and progress corrections. | M per checkpoint | Public administration and role design deferred |

## R1 — Isolate spectator bootstrap failures

**Confirmed defect.** A supported three-player CBC board reached 17 visible
cards through actual catalogue generation and public match selection/turn/battle
operations. A watcher joined during battle. At the next stable boundary,
bootstrap encoding raised `MatchError`, while deferred attachment catches only
`SpectatorError`. Real `OrdinaryTable.publish_match` invoked **none of six**
pending participant delivery calls; the same case with observers disabled
invoked all six. The probe used mocked transport/room delivery hooks, not sockets.
The observer remained registered instead of being evicted.

Start in [rooms/spectators.py](../src/tmserver/rooms/spectators.py) (`bootstrap`,
`publish`) and [rooms/table.py](../src/tmserver/rooms/table.py) (`publish_match`).
[events/spectators.py](../src/tmserver/events/spectators.py) shares this service;
the event consequence is supported by source inspection but was not separately
reproduced during this review.

Normalize expected bootstrap encoding failures into observer rejection/eviction.
Keep participant delivery, game state and reservations intact. Do not invent
snapshot fragmentation or enlarge the original packet limit.

**Done when:** immediate and deferred joins, ordinary and event publishers,
dense supported boards and a nearby valid-size control are covered. Unsupported
observation removes only that observer; all participant packets still arrive.
Encrypted ordinary and event continuation checks verify play can proceed.
Reproduction: `results/roadmap_protocol_repro.py` and its `.json` report.

## R2 — Preflight complete replies before mutation

This is one shared protocol boundary with two independently reproduced defects.

**Room presence/history:** an authenticated 456-byte `PD` request with a padded
decimal spelling passes parsing. Wrapping it in the larger `DD/DN/DC/PD` reply
fails encoding after assigning membership and advancing sequence/history. The probe
advanced sequence 101 → 102 despite rejection; another member's subsequent
replay failed on the stored oversized entry. Start in
[rooms/service.py](../src/tmserver/rooms/service.py) (`handle`, `delta`,
`wrap_delta`) and [rooms/packets.py](../src/tmserver/rooms/packets.py).

**Character profiles:** generic command encoding allows 469 bytes before adding
the five-byte `GTM0P` prefix. Complete replies of 470–474 bytes pass profile
restore validation but exceed the transport frame once its NOTICE prefix and
six checksum/terminator bytes are added. The current nine-byte source and
recipient nicknames produce a 37-byte NOTICE prefix. A legal ten-digit character
locator can also expand a previously valid stored projection by nine bytes.
Start in [profiles/characters.py](../src/tmserver/profiles/characters.py)
(`_response`, `handle`, `restore`) and
[protocol/commands.py](../src/tmserver/protocol/commands.py).

Define the **complete service reply** budget once: with the current nine-byte
nicknames, 469 bytes, leaving 464 for a five-byte-prefixed command body. The
shared constructor must validate or account for nickname lengths. Original
client send-bridge evidence independently places its 469-byte check on the fully
prefixed message. That transmit-side finding is not proof that every inbound
message uses the same limit. Keep the separate 447-byte named-body dialect and
the existing `!user@host` source prefix intact.

Use a small shared NOTICE constructor and family-aware reply preflight; 17
handwritten prefix constructions currently span seven runtime modules. Prepare
and validate room deltas before publishing membership/sequence/history. Validate
all supported profile locator projections before accepting an edit or restored
record. Preserve opaque profile values without silently truncating them.

**Done when:** exact 469/470-byte service boundaries, short/full-width locator
aliases, `DE`/`PD`, rejection rollback and another client's history replay pass.
Add encrypted profile read/restart and room replay checks. Existing valid replies
remain byte-identical. Exercise the shared guard in auction/ranking constructors
where applicable; do not merge their distinct parsers.
Evidence: `results/roadmap_room_budget_repro.json`,
`results/roadmap_profile_budget_repro.json`, and
`results/notice-budget-evidence.json` (original references and frozen boundaries).

## R3 — Make the verification claims repeatable

**Confirmed packaging gap.** A copied clean wheel with two actual stale build
modules added still passed [tools/check_wheel.py](../tools/check_wheel.py),
including installation and startup. The current `dist` wheel itself is clean.

First, make the checker compare the complete intended runtime manifest and file
bytes. Reject extra/missing modules, changed resources and duplicate archive
entries before installation. Provide a documented build from a fresh staging
directory so deleted modules cannot survive from an old build cache. Avoid a
hardcoded total file count. Acceptance includes the clean wheel plus deliberately
stale, missing-resource, changed-module and duplicate-entry fixtures.
Evidence: `results/stale-wheel-audit.log` and the contaminated copy under
`results/stale-wheel-audit/`.

Next, make [tools/check_collection.py](../tools/check_collection.py) reject
duplicate test IDs; today's IDs are unique, so this is a guard rather than a
current failure. Reuse a small set of fresh-process model/codec import-isolation
checks against both source and the installed wheel. Legitimate new tests must
not require updating a fixed expected count.

Finally, add a repeatable portable test lane and an explicitly selected disposable
PostgreSQL lane, preferably in CI. Reuse existing migration, writer exclusion,
rollback, lost-commit-acknowledgement and encrypted transaction tests. PostgreSQL
validation must fail if its tests unexpectedly skip. Ordinary JSON development
must remain independent of PostgreSQL. Never default this runner to a live save
or database. No CI workflow is currently checked in.

**Done when:** a clean checkout can run the documented checks; a release candidate
has a fresh full portable result, an explicit database result, and exact-source
wheel verification. PostgreSQL upgrade is repeatable and JSON/database round
trips preserve native profile bytes and outstanding claims.

## R4 — Detect stale rating history before play

**Confirmed workflow gap.** Switching from local-history to preserve mode,
playing, then re-enabling local-history can leave an enrolled account's history
behind its profile. [rankings/policy.py](../src/tmserver/rankings/policy.py)
correctly rejects that mismatch, but `quote` reaches the check at the completed
round boundary. Server construction accepts the restored state, and ordinary
admission does not check this coherence.

Add a read-only preflight when enabling local-history, identifying affected
account keys before stakes, card selection or gameplay. Reuse startup diagnostics
or an existing mapped admission failure; do not invent a wire error or silently
reset the rating history.

**Done when:** compatible and unenrolled accounts still work; stale enrolled
accounts fail early; checking changes no profile, account or journal. The
existing policy-switch regression in
[test_rating_policy.py](../tests/rankings/test_rating_policy.py) supplies a
starting fixture. An explicit repair policy is a separate R8 decision.

## R5 — Accept a recorded native candidate

Follow [NATIVE_TEST_CHECKLIST.md](NATIVE_TEST_CHECKLIST.md), recording server and
loader revisions, deployed loader hash, asset manifest and session IDs. The
first two user-operated passes cover CPU/rematch/shop/deck/reload, then
two-human play/trade/reload. Record three-player, events, auction/Prize Center
and spectator scenes separately. Agents do not control the desktop or game.

**Done when:** both core passes succeed for one recorded candidate. Optional
scenarios are separately marked accepted or unaccepted; failures identify their
first failing scene and relevant logs. Earlier successful play and headless
protocol tests do not mark this candidate accepted.

## R6 — Reduce responsibilities inside the largest services

**Implemented 2026-09-28:** match dispatch now delegates table messages and lobby
controls; ordinary table dispatch delegates query/observer paths. Shop behavior
is in `economy/shop.py`, receiving the staged draw callback per operation. Shared
inventory checks live beside account models, and delayed deck edits call their
codec directly. These are responsibility reductions, not a large net line-count
reduction. The following records the original review rationale.

**Optional refactors after the correctness work.** The directory layout is now
adequate. The remaining reading cost is concentrated in long stateful methods:

- [game/match.py](../src/tmserver/game/match.py): 1,999 lines; `handle` is 263.
  Extract two private helpers in the same class: table messages (viewer/exit,
  admission validation, Req, settings and discovery) and lobby controls (entry,
  roster, start/acceptance, departure/kick). Keep decode, seat lookup and departed
  guards in `handle`, before routing non-gameplay messages to those helpers.
  Preserve `None` (another service) versus `[]` (handled), validation order and
  one owner for match state. This can make `handle` roughly 115 lines without
  introducing dispatch sentinels or a wrapper for every branch.
- [rooms/table.py](../src/tmserver/rooms/table.py): 905 lines;
  `dispatch_table` is 294. In a separate change, extract query/observer branches,
  preserving lock boundaries and owner checks after awaited delivery.
- [economy/service.py](../src/tmserver/economy/service.py): 1,120 lines at review.
  Its `_init`, `handle` and `_edit` contain 139 function lines, but `_edit` also
  serves delayed trade/event deck edits. Extract shop behavior into a stateless
  `economy/shop.py` handler over the selected staged account, parsed message,
  catalogue, offers and draw function. Keep family recognition, parsing and
  account/reservation checks in EconomyService and preserve `Commerce.shop`'s
  staging/save boundary. Shared inventory validation belongs beside the models;
  delayed deck edits should use the existing edit codecs directly. Pass the
  staged draw function per operation: caching a callback to the original owner
  would break RNG rollback. No account owner should depend on shop behavior for
  a non-shop operation.

**Done when:** existing native fixtures, admission/CPU/rematch/observer/departure
and shop replay/rollback controls retain replies, ordering and saved state.
Make one behavioral extraction per commit. No generic state-machine framework,
new ORM/domain-model stack or promised multi-thousand-line reduction is needed.

## R7 — Expose all outstanding claims in offline inspection

**Confirmed feature omission.** [cli/admin.py](../src/tmserver/cli/admin.py)
reports balances, Prize Center and event grants, but omits auction payments,
refunds, won/returned cards and unaccepted match rewards. Existing journals
already expose these values. A probe with one pending returned card produced
an account summary with no indication of that claim.

Extend the read-only summary with remaining claim counts/amounts and stable
receipt IDs, distinguishing outstanding assets from owned inventory and balance.
Include interrupted-round status where useful.

**Done when:** fixtures for each claim kind report accurately before and after
partial collection. Inspection never expires auctions, recovers matches,
collects rewards or writes storage, and accepts absent/null optional journals.

## R8 — Decisions and research to keep separate

- **Rating-history migration:** decide whether operators supply exact missing
  history or deliberately begin a new local-policy epoch. Then add preview and
  atomic, idempotent application, preserving old result receipts. A scalar
  rating alone cannot reconstruct the historical ring. Depends on R4.
- **Spectator synchronization:** keep preview opt-in after R1. The original watch
  dialog has no recovered dismissal acknowledgment; successful network delivery
  is not proof that a native scene consumed an update. Research a native signal
  before considering a separately versioned loader-assisted acknowledgment.
  Acceptance needs slow-dialog/long-animation native tests.
- **Historical rules:** hosted rewards, rating formulas, rookie eligibility,
  event schedules, auction fees and participant resume remain evidence/policy
  questions. Do not describe invented choices as recovered behavior.
- Public hosting/authentication is a separate product milestone; current
  listeners use loopback and synthetic local accounts. Browser work, language
  rewrites, backups and performance projects remain outside this roadmap pass.

## R9 — Add an operator admin panel

Requested after this review. Follow [ADMIN_PANEL_PLAN.md](ADMIN_PANEL_PLAN.md):
start with server/account/room status and outstanding claims, using the same
read projections as offline administration. Add existing grant/progress actions
through shared transactions and audit receipts after the read-only panel passes.
The implemented optional Starlette Admin interface attaches to the running
server's state owner. It never opens another writer on the active save. The
framework supplies the interface, authentication integration and CSRF handling;
shared detached projections define balances, inventory and outstanding claims.
Its separate test lane and installed-wheel smoke keep the base server independent
of web dependencies. Prize Center actions now share strict requests, stale-preview
checks and atomic audit receipts between CLI and web. Explicit `--admin-actions`
enables these controls. Event grants now have the same shared preview, atomic
receipt and retry contract in the CLI and panel, including canonical card facts
and collection status. Eligibility and progress now use shared strict requests,
before/after previews and jointly committed event/audit journals. New CLI preview
and apply commands and the opt-in panel share those actions. Legacy commands remain
available for compatibility; they never acquire invented operator receipts.
Journal v3 reads earlier v1/v2 receipts unchanged and retains exact progress facts
and original eligibility history after later corrections. These explicit operator
actions do not infer retail event rules or grant assets automatically.
