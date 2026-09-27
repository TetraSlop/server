# PostgreSQL persistence

The server now supports PostgreSQL through SQLAlchemy Core, with Alembic
migrations. The existing JSON launcher remains available. Database mode is
explicit: it neither reads nor overwrites `local_state.json`.

The default JSON backend also checks each save against the file it previously
read or committed. If that file disappears or changes externally, it stops the
writer rather than merging stale memory or recreating a partial economy. Restart
from the intended save after resolving the conflict. Unchanged saves retain their
existing bytes without replacing the file.

JSON commits use a short operating-system lock on an empty adjacent
`<save-name>.lock` file. Leave this sidecar in place; the lock itself releases
when the file handle closes or the process exits. Writes use a unique temporary
file in the same directory, flush it, then atomically replace the save. Failed
writes clean up their own temporary file and require a fresh server. This is
protection for a local save, not a shared network-file or multi-server backend.

## Setup

Use Python 3.13 and PostgreSQL 16 or newer. From the server directory, install
the optional database dependencies in your environment:

```powershell
python -m pip install -e ".[postgres]"
$env:TM_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST:5432/tetramaster'
tmserver-db upgrade
```

Create the database and its credentials separately. Use a direct PostgreSQL
connection or a session-pooling endpoint: transaction-pooling proxies cannot
preserve the session writer lock. Run migrations with a schema-owning role.
Use TLS and the appropriate PostgreSQL URL parameters on a remote deployment.
Do not put real credentials in committed files or command-line arguments.

For a fresh economy, skip import. To move an existing save, stop the server that
owns it and import the intended save:

```powershell
tmserver-db import-json 'var/local_state.json'
tmserver
```

The importer validates every present service section with the existing domain
readers, retains pending claims and recovery journals, records a SHA-256 digest,
and verifies every generated native profile byte for byte after reading it back
from PostgreSQL. Missing service sections in older saves remain missing until the
server next saves. The importer refuses a database that already contains state,
including a repeated import. It never updates the source file.

For event saves, supply the same configuration to import and startup:

```powershell
tmserver-db import-json 'var/local_state.json' --events examples/events.example.json
tmserver --events examples/events.example.json
```

`TM_DATABASE_URL` selects database mode. An explicit `--state some-save.json`
selects JSON instead, even if that environment variable is set. Supplying both
`--state` and `--database-url` is rejected. All existing game options remain
available; protocol listeners still bind only to loopback.

The previous scratch-project launcher is external to this package and still uses
its existing backend and save. Switching that launcher is a separate integration
step; installing this package does not change it.

## Ownership and recovery

One server process owns one database. A session advisory lock rejects a second
writer immediately; migrations and import/export use the same lock and require
the server to be stopped. Keep the database store context open until the server
has completed shutdown, including any final table cleanup:

```python
with PostgresStateStore(url) as store:
    game = Server(store=store)
    try:
        await game.start()
        # Run the service.
    finally:
        await game.stop()
```

Full saves commit changed accounts, their ordered cards, all changed service
journals, and a revision check in one transaction. Unchanged cards and accounts
are not rewritten. Database bounds protect money, native identities and card
attributes. The domain still validates catalogue rules and economic invariants.

Ordinary shop and trade operations stage only the affected accounts, save them,
then publish live state. Their PostgreSQL writes do not build or compare a
whole-server snapshot. Trade negotiation and duplicate final Get replies do not
write. Ordinary matches also stage stakes, completed results, card prizes,
Continue edits and disconnect recovery. Their affected accounts and four match
journals commit together before live accounts, table reservations or completion
replies advance. Auctions, Prize Center and event claims also stage their
inventories, money and receipts. Profile edits, options, saved table settings,
event deck edits and account creation use scoped commits. The live request
handlers no longer call the full-save path; startup recovery and explicit
administrative calls can still use it.

Writes remain synchronous, and staging contexts never yield to another task.
On any save failure, admission closes and clients disconnect without a success
acknowledgement. Correct the database problem and start a fresh server. The old
writer never reconnects or retries against its potentially inconsistent memory.
If a COMMIT acknowledgement was lost, the fresh server reads the committed state
and existing durable receipts.

Interrupted matches retain the established behavior: refund unsettled stakes,
preserve completed results, and recover unclaimed cards through Returned Cards.
Boards, sockets and in-flight trade consent are not resumed.

For trusted local award administration, use the same transaction boundary as
the protocol handler:

```python
from tmserver.claim_transactions import prize_transaction

with prize_transaction(game, account_key) as prize:
    prize.grant_award(account_key, "local-award-001", top30=500)
```

The context saves the account and award journal before publishing the award.
The ID identifies the logical award and must be stable across retries. Direct
domain-service methods remain in-memory APIs; their caller owns persistence.

## Export

With the server stopped:

```powershell
tmserver-db export-json 'var/export.json'
```

Exports exclusively create a new file and refuse to overwrite an existing save.
The result can be opened by the JSON backend. After accepting database writes,
use a fresh export for a deliberate switch back; the pre-import save is stale.
Backup operations are outside this project setup.

## Tests and current limits

```powershell
$env:TM_TEST_DATABASE_URL = 'postgresql+psycopg://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/postgres'
python -B -m unittest discover -s tests -t . -p '*postgres*.py' -v
```

Tests require permission to create databases and terminate their own test
connections. Each test creates a random `tm_test_...` database and drops only
that database on cleanup. The administrative database's tables are not changed.
Without this environment variable, PostgreSQL tests are skipped.

This is a complete alternative save backend for the existing single-process
server. It is not a distributed architecture; the bounded measurements are documented
in [load testing](LOAD_TESTING.md). Full administrative
saves still visit all accounts; database I/O blocks the event loop.
Each service journal is currently one versioned JSONB document. Ordered card
slots preserve duplicate copies, but do not yet provide permanent per-copy IDs.
See [PERSISTENCE_DESIGN.md](PERSISTENCE_DESIGN.md) for those deliberate boundaries.
