# Tetra Master server

[![Checks](https://github.com/sruon/tmserver/actions/workflows/checks.yml/badge.svg)](https://github.com/sruon/tmserver/actions/workflows/checks.yml)

Python implementation of the original PC Tetra Master game and profile services.
The native client, loader and original executable research environment are separate.
The server supports local test accounts and automatic Discord accounts through
Supabase. Its listeners bind to loopback; hosted use requires a TLS reverse proxy.

For the integrated native setup in the sibling `tmloader` checkout, use
**`Play.cmd`**. [Local play](docs/LOCAL_PLAY.md) covers one/two/three windows and
the [native test checklist](docs/NATIVE_TEST_CHECKLIST.md). `Check Setup.cmd`
validates the setup without opening the game.

The original Computer menu supports one or two CPU opponents from its 30-entry
roster, including rematches and switching between 16-cell and 25-cell games.
Reach it through **Local Room → an empty table → Computer**. The launcher also
provides [Open Tables, Classic, Special and Quick Play rooms](docs/ROOMS.md).
Encrypted gameplay, rewards, recovery and spectators are tested; the updated
three-player menu still needs a native screen check.

## Development

Build artifacts and release instructions are in [Checks and releases](docs/CI.md).

Python 3.13 or newer:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m tools.check_collection
.venv/Scripts/python -m tools.check_tests portable
.venv/Scripts/ruff check src tests tools
.venv/Scripts/python -m tools.build_wheel
.venv/Scripts/python -m tools.check_wheel dist/tmserver-0.1.0-py3-none-any.whl
```

On Linux, use `.venv/bin/python` and `.venv/bin/ruff` instead. Pydantic validates
room/event configuration and auction save records; Construct defines the binary
directory records. Both install with the package. The admin panel, PostgreSQL
and original-rules emulator remain optional dependencies.

The portable lane runs without a database. It checks source import boundaries,
rejects duplicate test IDs and fails on unexpected skips. Its only allowed skip
is the administrator's optional PostgreSQL import compatibility check when those
dependencies are absent. Wheel verification checks the exact current package
manifest and bytes before installing into a fresh environment; the build helper
uses a fresh staging directory so old `build/` files cannot leak into releases.

To run the PostgreSQL lane, install `".[dev,postgres]"` and start a Docker daemon,
then run `python -m tools.check_tests postgres`. It creates a temporary PostgreSQL
16 container with a random password and loopback port, runs every database test
with no skips allowed, and removes the container and its data afterward. Neither
lane inherits `TM_DATABASE_URL` or `TM_TEST_DATABASE_URL`. CI runs the portable
lane on Windows and Linux, and separate PostgreSQL, admin and auth lanes on Linux.
The optional admin lane requires `".[dev,admin]"` and runs with
`python -m tools.check_tests admin`; it permits no skipped tests.

```powershell
.venv/Scripts/tmserver --list-accounts
.venv/Scripts/tmserver --state var/local_state.json --log-dir var
```

This starts only the server. It does not launch the game or control the desktop.
State and logs default to `var/` in the working directory, outside the installed
package. `Play.cmd` uses this package and its own `var/local_state.json`; the old
scratch launcher still uses its earlier save.
Use `--help` for rooms, event configuration, human tables, prizes and ratings.
Optional [weekly rankings](docs/WEEKLY_RANKINGS.md) record future local play with
`--ratings local-history --weekly-rankings`.
For saved-account inspection, a recovery preview, queued prizes and explicit
event progress, grants or eligibility, use
[offline administration](docs/ADMIN.md). `tmserver-admin` requires an explicit
save target and refuses to share it with a running packaged server.

To inspect the running server in a browser, install the optional admin panel:

```powershell
.venv/Scripts/python -m pip install -e ".[admin]"
.venv/Scripts/tmserver --state var/local_state.json --admin-port 8080 --admin-password-file C:/private/tm-admin-password.txt
```

Put your operator password on one line in that file. Open
`http://127.0.0.1:8080/admin/` and sign in as `admin` (or set `--admin-user`).
Alternatively, supply the password through `TM_ADMIN_PASSWORD`. The panel is
off by default, binds only to loopback, and shows live accounts, inventories,
pending claims, room status and searchable operator receipt history. It shares
the game's state owner and is read-only by default.
Add `--admin-actions` to enable previewed, audited Prize Center awards, event
grants, eligibility and progress corrections. Each action and its operator receipt
commit together; retrying a request cannot apply it twice.
PostgreSQL installations need `tmserver-db upgrade` before starting this version.
See [admin panel](docs/ADMIN_PANEL_PLAN.md) for access, packaging and available actions.

The optional [hosted authentication prototype](docs/AUTHENTICATION.md) adds
Discord sign-in through Supabase, automatic saved accounts and authenticated
WebSocket game/profile streams. Install `".[auth]"` and supply `--auth-config`
to enable it. The [loader contract](docs/LOADER_AUTH_SPEC.md) defines browser
sign-in, tickets, session renewal and byte forwarding. Live provider/loader
sign-in is confirmed; complete native hosted-match acceptance and public TLS
deployment remain pending.

For PostgreSQL, set `TM_DATABASE_URL`, run `tmserver-db upgrade`, then `tmserver`.
An explicit `--state` chooses JSON instead. Database tests require a disposable
PostgreSQL host through `TM_TEST_DATABASE_URL`; each creates and drops its own
random database. See [persistence](docs/PERSISTENCE_DESIGN.md).

## Layout

- [src/tmserver/](src/tmserver/): feature packages listed below.
- [tests/](tests/): unit and encrypted integration tests grouped by the same
  features; shared frozen inputs stay in `tests/fixtures/`.
- `examples/`: optional room, shop and event configurations.
- [docs/](docs/README.md): current architecture, persistence and operating instructions.
- `docs/research/`: historical protocol and implementation notes.
- `research/`: original-client validation sources and migration provenance.
- `tools/`: repeatable development and load-test commands.
- `results/` and `var/`: ignored generated output and local state.

| Package | Responsibility |
| --- | --- |
| `cli` | Server, local-play and offline-admin entry points. |
| `admin` | Shared read projections and the optional authenticated operator panel. |
| `auth` | Optional Supabase verification, play capabilities and account-bound WebSocket transport. |
| `network` | Server ownership, connections, dispatch, chat and diagnostics. |
| `protocol` | Encryption, command envelopes, identities and table controls. |
| `cards` | Shared card values, catalogue and inventory edits. |
| `game` | Match phases, computer opponents and recovered game algorithms. |
| `rooms` | Ordinary room configuration, tables, admission and spectators. |
| `events` | Configured event rooms, matchmaking, results and card grants. |
| `economy` | Accounts, shop, trade, auctions, prizes and transaction boundaries. |
| `profiles` | Profile transport, character records, options and saved preferences. |
| `rankings` | Result statistics, ratings and optional weekly projections. |
| `storage` | JSON/PostgreSQL stores, validation, recovery and database migrations. |
| `data` | Immutable packaged protocol tables and their resource reader. |

The [architecture guide](docs/ARCHITECTURE.md) identifies the files to change for
each responsibility and explains ownership across them. Shared dataclasses live
in feature `models.py` modules; `packets.py` owns wire encoding and parsing.
Services own behavior and transaction modules
coordinate durable changes. SQL stays in `storage/`, behind the same contract
as JSON. Import models and codecs directly from their owners.
Larger subfeatures follow the same convention in their own package, such as
`economy/auctions/{models,packets,service,ledger}.py`, with matching test folders.

Python imports now use
these package paths, for example `tmserver.network.server.Server` and
`tmserver.game.match.MatchService`. The former flat module paths have no
compatibility shims; custom Python integrations must update their imports.
The `tmserver`, `tmserver-db`, `tmserver-play` and `tmserver-admin` commands,
`python -m tmserver`, and `Play.cmd` remain the supported entry points.

Build a wheel with `python -m tools.build_wheel`. Runtime data and migrations are included;
tests, research scripts, client binaries, captures and live saves are excluded.
Historical online reward policies and full native UI parity remain unproved.

An optional [original-code rules prototype](docs/ORIGINAL_RULES_PROTOTYPE.md)
executes the client's battle and ordinary AI instructions through a native 32-bit
C++ bridge, with an optional Unicorn backend for comparison. It requires a
separately supplied unpacked code image. Python remains the default; the explicit
`--rules-backend native-hybrid` option injects the native routines into ordinary
and event matches. Special/three-player AI and the match state machine remain
Python. See the linked guide for required paths, supported hosts and lifetime.

## Ownership and load testing

Services own their staged copies and publication; the concrete transaction
contexts own commit order. See [architecture](docs/ARCHITECTURE.md).

```powershell
.venv/Scripts/python -m tools.load_test --backend json --players 32 --seconds 10 --output results/json-32.json
```

The harness starts a private server, authenticates encrypted clients, measures
latency and storage stalls, then verifies saved updates after reload. It uses
temporary state. See [load testing](docs/LOAD_TESTING.md) for PostgreSQL, dormant
account populations, measurements and the limits of this workload.

The [validation record](docs/VALIDATION.md) covers the portable suite,
PostgreSQL failure/recovery tests and clean wheel installation. The
[measured baseline](docs/LOAD_TESTING.md#baseline-2026-09-27) records current
throughput and storage stalls; it is not a production player-capacity claim.
