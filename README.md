# Tetra Master server

Python implementation of the original PC Tetra Master game and profile services.
The native client, loader and original executable research environment are separate.
The server currently accepts synthetic local accounts and binds to loopback.

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

Python 3.13 or newer:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,postgres]"
.venv/Scripts/python -m unittest discover -s tests -t .
.venv/Scripts/ruff check src tests tools
.venv/Scripts/python -m build --wheel
.venv/Scripts/python -m tools.check_wheel dist/tmserver-0.1.0-py3-none-any.whl
```

On Linux, use `.venv/bin/python` and `.venv/bin/ruff` instead. PostgreSQL is
optional; a JSON installation has no third-party runtime dependencies.

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
each responsibility and explains ownership across them. Within the main match,
economy and event features, `models.py` owns shared dataclasses and `packets.py`
owns wire encoding and parsing; services own behavior and transaction modules
coordinate durable changes. SQL stays in `storage/`, behind the same contract
as JSON. Import models and codecs directly from their owners.

Python imports now use
these package paths, for example `tmserver.network.server.Server` and
`tmserver.game.match.MatchService`. The former flat module paths have no
compatibility shims; custom Python integrations must update their imports.
The `tmserver`, `tmserver-db`, `tmserver-play` and `tmserver-admin` commands,
`python -m tmserver`, and `Play.cmd` remain the supported entry points.

Build a wheel with `python -m build`. Runtime data and migrations are included;
tests, research scripts, client binaries, captures and live saves are excluded.
Historical online reward policies and full native UI parity remain unproved.

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
