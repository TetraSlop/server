# tmserver handoff — 2026-09-29

## Purpose and starting point

Continue work on the Python Tetra Master server, with particular attention to
retail behavior missing from the otherwise broad protocol implementation.
This handoff records findings and recommended work; the new fidelity fixes
below have **not** been implemented.

- Repository: https://github.com/sruon/tmserver
- Local checkout: `C:/Users/sruon/Documents/GitHub/tmserver`.
- Runtime baseline: `e38959af8ba54526c9bdc9f84d6ed1d1b4d75784`, published to `main`.
- Local branch at handoff: `codex/packaging-readability-load`; it tracks
  `origin/main`. Inspect current status before editing; another task may advance it.
- Native-only implementation: `e3192ab`; test-lane correction: `e38959a`.
- Last verified [GitHub Checks run](https://github.com/sruon/tmserver/actions/runs/36541507806)
  passed on that runtime baseline.

The user first requested an aggressive size-reduction analysis, then supplied
the [PlayOnline Tetra Master wiki page](https://finalfantasy.fandom.com/wiki/Tetra_Master_%28PlayOnline%29)
for a fidelity comparison. This changed the emphasis: several proposed cuts
would remove meaningful parts of the original collection economy. Do not treat
the earlier analysis as authorization to delete those features.

## Working constraints and decisions

- Work on **tmserver only**. Another task owns the sibling `tmloader` repository.
  Coordinate interface changes through [LOADER_AUTH_SPEC.md](LOADER_AUTH_SPEC.md).
- The user explicitly asked agents to stop controlling their desktop. Do not
  open or operate the game, move the mouse, or automate screenshots. Native UI
  acceptance is user-operated; headless protocol tests are appropriate.
- Native execution is the required rules path. Do not restore Unicorn, the
  duplicated Python battle/AI/growth implementations, or silent fallbacks.
- Python remains the server language. Readability and explicit ownership matter
  more than cosmetic line-count reductions. Pydantic and Construct are adopted.
- The loader/client bundle does not contain the server. Public hosting is separate.
- Discord login through Supabase is the chosen authentication route. Accounts
  should be created automatically and use the authenticated display name.
- The user authorized publishing server code to the GitHub repository. No release
  PR has been merged automatically and no public server deployment is complete.
- Recurring automation was disabled. Do not recreate it. Do not check usage or
  credits; the user specifically asked us to stop doing that.
- Preserve existing accounts, cards and pending claims. Do not reset the real
  save to make a new policy easier to implement.

## What currently exists

The server has encrypted legacy game/profile transport, a Discord-authenticated
HTTP/WebSocket gateway, persistent identities and inventories, room/table
discovery, COM and human matches, two-/three-player boards, special effects,
growth, card prizes, wagers, shop packs, trading, auctions, Prize Center,
rankings, events, spectators, and an optional administration panel.

Feature presence is not proof of complete retail fidelity or native UI acceptance.
Several policies are explicitly local choices, and some features are opt-in.

### Native rules

`native/original_rules` is a C++23 x86 bridge, **ABI 3**. It calls original
instructions for battle calculations, card growth, target selection, and
ordinary/special/three-player COM hand and placement decisions.

Python still owns board effects and generation, match sequencing, departure
handling, ownership settlement, server policy, storage and transport. Original
client instructions do not reveal missing historical server reward formulas.

The normal server requires **32-bit Windows Python** and the verified unpacked,
RVA-indexed original image. Native Linux hosting is not supported by this bridge.
The loader's packed `TM_native.dll` is not interchangeable with the mapped image.
See [ORIGINAL_RULES_PROTOTYPE.md](ORIGINAL_RULES_PROTOTYPE.md) despite its historical name.

`--rules-backend` was removed. The CLI requires `--rules-image` and
`--rules-bridge`, or `TM_ORIGINAL_IMAGE` and `TM_NATIVE_BRIDGE`. Bad files,
architecture or ABI fail before serving. Direct calculation APIs delegate to a
lazy process-owned native context; inspection tools can still import without it.

### Authentication and persistence

Verified Discord/Supabase identities automatically obtain a durable account at
first Play. Manual pilot bindings have been migrated; do not reintroduce manual
enrollment as the normal flow. Display-name import, ownership binding, tunnel
admission, remembered loader sign-in and server account creation have been exercised.

JSON and PostgreSQL stores exist. Account changes and related journals must
commit before replies are delivered. PostgreSQL currently uses a single-writer
lease, not a multi-server concurrent-writer architecture. Native PostgreSQL
requires a 32-bit libpq client; the local native database checks passed against
a disposable PostgreSQL 16.15 host. That test client had TLS disabled for loopback;
production database TLS remains unverified.

### Size

At the runtime baseline: **24,279 Python lines in 144 files**, including comments
and blanks. Tests: 44,951; tools: 1,778; research Python: 805, all counted separately.
The native cleanup removed 867 runtime Python lines and 23,868 research lines;
the bridge grew by 12 lines. Do not present the research deletion as runtime savings.

## Local environment and running pilot

Paths below are local, ignored development files, not distributable requirements.

| Purpose | Path relative to the checkout |
| --- | --- |
| Preferred standard x86 environment | `build/server-x86/Scripts/python.exe` |
| Older embedded x86 environment used by the current process | `build/python-x86/python.exe` |
| 64-bit tooling environment; not valid for gameplay | `.venv/Scripts/python.exe` |
| Original mapped image | `var/native/TM_emulated_mapped.dll` |
| ABI 3 bridge | `var/native/tm_original_rules.dll` |
| Pilot authentication configuration | `var/auth.json` |
| Pilot save | `var/auth-pilot/state.json` |
| Pilot process record | `var/auth-pilot/server.process.json` |
| Local restart helper | `var/auth-pilot/start-native.ps1` |
| Pilot logs | `var/auth-pilot/native-server.stdout.log`, `.stderr.log` |

At handoff, gateway `http://127.0.0.1:8081/v1/auth/config` returned HTTP 200 and
port 8081 belonged to PID **78592**. Treat the PID and status as a dated observation;
recheck before any process action. The gateway uses ephemeral internal game/profile
ports, automatic registration, wagers, and `examples/auto_rooms.example.json`.
The current restart command enables wagers but **does not enable `--card-prizes`**.

The restart helper verifies the recorded process command before stopping it,
preserves the configured save, starts a hidden process, and uses the preferred
standard x86 environment on its next run. It interrupts the current pilot, so
do not restart it merely to inspect code or run unrelated tests.

For an isolated native test shell, from the checkout:

```powershell
$env:TM_ORIGINAL_IMAGE = (Resolve-Path var/native/TM_emulated_mapped.dll).Path
$env:TM_NATIVE_BRIDGE = (Resolve-Path var/native/tm_original_rules.dll).Path
& build/server-x86/Scripts/python.exe -m unittest research.native_backend_tests -v
```

Use disposable state for test servers. Never use the pilot save for fault-injection
or destructive fixtures. Authentication configuration, tokens, saves, original
images and generated binaries are excluded from source publication.

## Validation and CI

- All **1,555 maintained cases** are accounted for across local runs: 1,554 passed
  and one documented optional PostgreSQL import check skipped.
- This comprises 1,475 non-database cases, 78 image-free PostgreSQL cases, and
  two native PostgreSQL complete-match cases. It is not a claim that every test
  ran in one invocation after every subsequent test-manifest edit.
- Seventeen native acceptance tests verify **8,067 frozen results and RNG draw
  sequences**. Extended special/three-player AI also matched 768 comparison cases
  before the duplicate Python scorer was deleted.
- A clean x86 wheel install passed native startup, admin, registration, tunnel
  and revocation checks. Provider identity is substituted in those HTTP tests;
  the user's earlier real Discord sign-in is separate evidence.
- `tests/native_modules.json` assigns modules requiring the original image to
  the native lane. In particular, successful local setup now performs native
  preflight, so `tests.storage.test_startup_snapshot_validation` belongs there.
- Main GitHub Checks passed: Linux/Windows image-free tests, auth/admin,
  PostgreSQL, lint/discovery, packaging, and bridge build/ABI checks.
- Full hosted original-code acceptance is a separate workflow. It needs the
  **`TM_ORIGINAL_IMAGE_URL` repository secret**, which has not been configured.
  Releases require that native workflow as well as ordinary Checks. A green
  ordinary Checks run is not a green hosted original-image acceptance run.
- Release-please maintains a release PR; do not merge it merely to finish this
  handoff. Original game images are not included in server release artifacts.

Useful commands, with the appropriate interpreter and dependencies:

```powershell
& .venv/Scripts/python.exe -m ruff check src tests tools
& .venv/Scripts/python.exe -m tools.check_collection
& .venv/Scripts/python.exe -m tools.check_tests portable
& build/server-x86/Scripts/python.exe -m tools.check_tests native
```

Use focused tests while changing one feature. Native and native-postgres tests
must execute real native rules; do not make them portable by substituting Python
calculations. Database lanes require disposable hosts; see [CI.md](CI.md).
Evidence and qualifications are recorded in [VALIDATION.md](VALIDATION.md).
Rendered client acceptance remains in [NATIVE_TEST_CHECKLIST.md](NATIVE_TEST_CHECKLIST.md).

## New fidelity backlog

The linked wiki supplied a feature checklist. Its indexed article text and linked
[COM opponent table](https://finalfantasy.fandom.com/wiki/Tetra_Master_%28PlayOnline%29_COM_opponents)
were read and compared with source. Treat community descriptions as leads;
recovered instructions and observed native behavior remain necessary evidence.

### P1 — Guild onboarding and guild-aware COM generation

**Confirmed implementation gaps.**

- `economy/service.py:provision` defaults to `guild=1`. The authenticated first-login
  path in `accounts/provisioning.py` leaves that default in place.
- `initialize_profile` already supports an idle, unregistered guild-0 account
  choosing guild 1..3. It refuses switching an already selected guild.
- `game/match.py:_prepare_npcs` calls `prepare_opponents` without a guild;
  `game/opponents.py` defaults it to 0, which is the unrestricted catalogue filter.
- A memory-only reproduction with CRT seed 1 and opponent 30 generated model 83
  through the current path; guild 1's filter excludes it. Supplying guild 1 to
  the helper removed that forbidden category.

**Work:** connect fresh-account initialization to native guild selection and
carry the authenticated account's guild into COM pool generation. Decide the
timing of the initial ten cards: generating them before guild selection and then
only changing the guild would leave inconsistent starter inventory. Existing
registered accounts must retain their guild and collection.

**Acceptance:** all three new-account choices, retries, reconnect before/after
choice, save failure and existing-account login; shop and COM filter checks for
each guild. Obtain user-operated native confirmation of the selection screen.

### P1 — Ordinary board generation and first-player selection

**Confirmed local shortcut.** In `game/match.py:_select`, automatic generation
runs only when at least one of `st/cb/ca` is enabled. Otherwise it creates an
empty board and sets `active=0`. Thus classic rooms always start the first seat.
`game/boards.py` already contains the recovered ordinary generator, including
walls and first-player selection; its policy is documented as local simulation.

**Work:** separate deliberate fixed/practice boards from normal generated play.
Verify normal zero-special-flag generation against the client, then use it without
requiring special tiles. Do not change recovered random draw order casually.

**Acceptance:** legal 4x4/5x5 boards with all special flags off, deterministic
seed replay, varied starting seats, rematches, and preserved explicit fixture
boards. Check actual client start packets and rendering separately.

### P1 — Retail COM economics and deliberate forfeits

**Existing mechanisms, incomplete policy.** Wagers and card transfers are opt-in;
the current pilot enables only wagers. `departure_supported` permits continuation
only for non-economic, unrated practice. Other Quit/Break paths finish the match;
`MatchEconomy.abort` refunds unfinished escrow and recovers earned assets.
There is no completed economic forfeiture path matching the article's COM loss
description. That safe recovery behavior must not be replaced indiscriminately.

**Work:** define a retail COM preset using existing prize/settlement machinery;
recover and implement intentional Quit penalties separately from broken transport,
timeouts and server/storage failure. Verify whether COM wager fields must equal
the selected opponents' listed amounts: current `_com_game` validates range and
enabled policy, but does not establish that relationship. The opponent table
corroborates the recovered names, average ranks and wager-like values; exact
two-opponent and rematch rules still need client evidence.

**Acceptance:** two-/three-player COM, normal/perfect card awards, deliberate
quit in each supported phase, repeat Quit, concurrent disconnect, server restart,
failed commit and overflow. No duplicated reward, lost unrelated cards or penalty
for an uncommitted server-side failure. User-operated result/quit UI checks remain.

### P2 — Champion deck in the shop

**Missing ranking-to-shop connection.** `ShopOffers` can expose and name the
special pack. `economy/shop.py` nevertheless generates purchases through the
static catalogue; no ranking winner's selected deck supplies its contents.

**Work:** investigate how the champion and deck are chosen, when the offer is
snapshotted, which stats/arrows are retained or regenerated, and how its native
price/name fields behave. The article does not specify those policies. Reuse
existing Buy/Get transaction semantics after resolving them.

**Acceptance:** correct selected source, stable in-progress purchases across
rank/deck changes, capacity checks, charge once, and persisted delivery once.

### P2 — Ranking and Prize Center progression

**Partial.** Ranking projections and Prize Center spending work. Weekly ranking
policy is explicitly local; Best Rookies is empty. `grant_award` is an operator
API, and automatic ranked/lucky awards are not implemented.

**Work:** research qualification, award amounts, periods and rookie definitions.
The supplied article confirms the feature but does not recover those formulas.
If local policy is chosen, label it clearly and persist idempotent award receipts.
Do not advertise invented values as recovered retail rules.

**Acceptance:** one award per account/period, restart and rollover safety,
eligibility and privacy, redemption, replay and joint account/journal rollback.
See [WEEKLY_RANKINGS.md](WEEKLY_RANKINGS.md).

### P2 — Historical zones, rooms and tables

**Partial infrastructure; local content.** Ordinary configuration publishes rooms
under Local Lobby. The example has one table per added room; additional tables
are already supported. The original language-zone layout and room presets are
not recreated. The article describes sixteen tables per room.

**Work:** add an evidence-backed topology/preset configuration, determine the
ordinary-zone routing changes needed, and review occupancy limits before assuming
existing local capacities support it. Avoid inventing novice/veteran eligibility
thresholds merely from room names. Keep COM capability and fixed-rule metadata
consistent with the native menu; see `COMPUTER_ROOM_RULES.md` in research notes.

**Acceptance:** independent discovery and membership per zone/room, table counts,
occupied/free/setup states, reservations, restrictions, spectator flags and COM
availability. Much of the table work should be configuration reuse, not a second
room implementation.

### Investigation — Resolve wiki/client discrepancies

- **Winner-selected stat increase:** the article describes a choice. Our
  `_complete_cards` applies recovered automatic result growth. No separate
  choice flow was identified in this audit. Inspect the result UI/message path
  before implementing an extra growth grant.
- **Fixed card types:** the article says types cannot change. The native growth
  routine demonstrably contains promotion, and our server invokes it. Establish
  whether that path belonged to retail online play rather than assuming the wiki
  or a surviving local routine settles the question alone.
- **Double Up:** the article's description differs from the recovered wager
  doubling in `_continue`. Preserve the established protocol behavior while
  investigating; do not reinterpret this field as player count.

Also include the Range display and deck-set controls in native UI acceptance.
Their mention in the article is not evidence of missing server handlers: the
original client owns presentation, and persistence for options/decks already exists.

## What not to rebuild from scratch

The backend already represents the 250-card catalogue, 30 COM opponents,
4x4/5x5 play, special effects, normal/perfect and three-player card-prize ordering,
shop/trade/auction protocols, Prize Center, table restrictions and spectating.
Separate missing retail policy from missing protocol, and missing UI verification
from missing code. Use the existing journal and transaction boundaries.

The earlier aggressive-cut analysis estimated 16–18k runtime Python lines for
a reduced feature set, or roughly 13–16k with gameplay/reward cuts. Those are
unimplemented estimates. Events alone occupy 3,509 lines, plus 540 dedicated
event-admin lines; auctions/trading, advanced rankings and observers were other
candidates. The wiki comparison makes clear that several cuts would change the
collection loop. Its silence about events also does not prove they are unnecessary;
the repository contains recovered event-protocol evidence.

Refactoring opportunities remain bounded: snapshot/restore methods total about
950 lines and staging/publishing methods 161. Many contain necessary invariants.
Moving more client calculations native may remove hundreds of Python lines while
adding bridge code; do not promise a many-thousand-line reduction from that alone.

## Suggested next work sequence

1. Read this handoff, inspect the current tree, and preserve the existing pilot.
2. Implement and validate guild onboarding plus COM filtering as one coherent change.
3. Correct generated ordinary starts with original-code evidence and focused tests.
4. Resolve COM stakes/forfeit semantics, then implement the durable settlement path.
5. Address champion shop content and ranking rewards after their policies are established.
6. Add historical topology and obtain user-operated native acceptance on a recorded
   server/loader combination. Continue separate public TLS/deployment work only
   when it is the active task; the current gateway is loopback-only.

Related references: [ARCHITECTURE.md](ARCHITECTURE.md), [ROADMAP.md](ROADMAP.md),
[AUTHENTICATION.md](AUTHENTICATION.md), [CI.md](CI.md),
[research/SIZE_REVIEW.md](research/SIZE_REVIEW.md), and
[research/RULES_PROVENANCE.md](research/RULES_PROVENANCE.md).
Research notes retain historical paths and superseded backend descriptions;
use current source and the dated native guide for execution instructions.
