# Validation

What the test lanes cover, and what they do not. How to run them is in
[checks and releases](CI.md).

## Test lanes

| Lane | Needs | Covers |
| --- | --- | --- |
| `portable` | Python 3.13, `.[dev]` | Protocol codecs, services, JSON storage, encrypted loopback sessions, tooling. Runs on Windows and Linux. |
| `admin` | `.[dev,admin]` | The admin panel, its sign-in, audited actions and receipts. |
| `auth` | `.[dev,auth]` | Supabase verification (provider mocked at the HTTP boundary), tickets, WebSocket game and profile streams, automatic registration. |
| `postgres` | `.[dev,postgres]`, Docker | Joint account and journal commits, rollback, lost commit acknowledgements, writer exclusion and migrations, on a disposable PostgreSQL 16. |
| `native` | 32-bit Windows Python, the game image, the bridge | Gameplay through the native rules, startup preflight, computer and human matches. |
| `native-postgres` | both of the above | Two complete matches stored in PostgreSQL. |

No lane may skip a test, except the portable lane's optional PostgreSQL import
check when those packages are absent. `tests/native_modules.json` assigns
image-dependent modules to the native lane, and the collection check fails if a
module has no lane.

`research/native_backend_tests.py` checks 8,067 frozen results and random-draw
sequences from the client's own routines, including special-board and
three-player computer moves.

The wheel check rebuilds from fresh sources, compares every packaged file with
the source tree, then installs it into a new environment and starts the base,
admin and auth services.

## Last full run

On 2026-11-21, on Windows, with the native lane on 32-bit Python and the native
bridge (ABI 3):

- Portable 912, admin 43, auth 50 and PostgreSQL 78 tests passed with no skips.
- The native lane passed 476 tests, with its one allowed skip, and the 17 native
  backend tests passed.
- The 2 native PostgreSQL tests were not run; they last passed on 2026-09-29.
- The wheel built from fresh sources matched the source tree, installed cleanly
  and passed the base, admin and auth checks.

The PostgreSQL client ran against a loopback server with TLS off, so TLS to a
database is not tested.

## Not covered

- What the game client shows on screen. Use the
  [native test checklist](NATIVE_TEST_CHECKLIST.md).
- Live Discord sign-in. Only the provider's HTTP responses are mocked.
- Public hosting behind TLS.
- Original server rules that the client does not contain, such as reward and
  rating formulas. See [rules provenance](RULES_PROVENANCE.md).
- Player capacity. The [load tests](LOAD_TESTING.md) measure throughput and
  storage stalls on one machine.
