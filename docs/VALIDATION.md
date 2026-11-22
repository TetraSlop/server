# Validation

What the test lanes cover, and what they do not. How to run them is in
[checks and releases](CI.md).

## Test lanes

| Lane | Needs | Covers |
| --- | --- | --- |
| `portable` | Python 3.13, `.[dev]` | Protocol codecs, services, gameplay rules, computer and human matches, encrypted loopback sessions on an in-memory store, tooling. Runs on Windows and Linux. |
| `admin` | `.[dev,admin]` | The admin panel, its sign-in, audited actions and receipts. |
| `auth` | `.[dev,auth]` | Supabase verification (provider mocked at the HTTP boundary), tickets, WebSocket game and profile streams, automatic registration. |
| `postgres` | `.[dev,postgres]`, Docker | Joint account and journal commits, rollback, lost commit acknowledgements, writer exclusion and migrations, on a disposable PostgreSQL 16. |

No lane may skip a test, and the collection check fails if a module has no lane.

`tests/game/test_rules_vectors.py` checks the Python rules against 8,067 frozen
results and random-draw sequences from the client's own routines, including
special-board and three-player computer moves. The growth vectors were recorded
at 64-bit (and 600 at 53-bit) x87 precision and are checked at that precision;
the server grows cards at the client's 24 bits, checked against a digest of all
6,248 growth cases run through the client's code at 24 bits.

The wheel check rebuilds from fresh sources, compares every packaged file with
the source tree, then installs it into a new environment and starts the base,
admin and auth services.

## Last full run

On 2026-11-22, on Windows with 64-bit Python 3.13 and fresh environments:

- Portable 1,292, admin 48, auth 67 and PostgreSQL 75 tests passed with no
  skips, covering all 1,482 collected tests. The portable lane includes the
  frozen client vectors and the golden save; the PostgreSQL lane includes two
  complete encrypted matches.

The PostgreSQL client ran against a loopback server with TLS off, so TLS to a
database is not tested.

## Not covered

- What the game client shows on screen. Use the
  [native test checklist](NATIVE_TEST_CHECKLIST.md).
- Live Discord sign-in. Only the provider's HTTP responses are mocked.
- Public hosting behind TLS.
- Original server rules that the client does not contain, such as reward and
  rating formulas. See [rules provenance](RULES_PROVENANCE.md).
- Player capacity. Throughput and storage stalls are not measured.
