# Tetra Master server

Python implementation of the original PC Tetra Master game and profile services.
The native client, loader and original executable research environment are separate.
The server currently accepts synthetic local accounts and binds to loopback.

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
package. The old launcher and save are untouched; adopting this server is explicit.
Use `--help` for rooms, event configuration, human tables, prizes and ratings.

For PostgreSQL, set `TM_DATABASE_URL`, run `tmserver-db upgrade`, then `tmserver`.
An explicit `--state` chooses JSON instead. Database tests require a disposable
PostgreSQL host through `TM_TEST_DATABASE_URL`; each creates and drops its own
random database. See [persistence](docs/PERSISTENCE_DESIGN.md).

## Layout

- `src/tmserver/`: server, game rules, protocol handlers, persistence and packaged data.
- `tests/`: portable unit and encrypted integration tests, with frozen fixtures.
- `examples/`: optional room, shop and event configurations.
- `docs/`: current architecture, persistence and load-test instructions.
- `docs/research/`: historical protocol and implementation notes.
- `research/`: original-client validation sources and migration provenance.
- `tools/`: repeatable development and load-test commands.
- `results/` and `var/`: ignored generated output and local state.

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
