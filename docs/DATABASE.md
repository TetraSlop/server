# PostgreSQL persistence

The server saves to PostgreSQL through SQLAlchemy Core, with Alembic migrations.
There is no other store: `tmserver` and `tmserver-admin` refuse to start without
a database URL. A JSON save from an older version cannot be imported.

## Setup

Use Python 3.13 and PostgreSQL 16 or newer. From the server directory, install
the database dependencies in your environment:

```powershell
python -m pip install -e ".[postgres]"
$env:TM_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST:5432/tetramaster'
tmserver-db upgrade
tmserver
```

The game server runs in 32-bit Python, and psycopg needs a 32-bit `libpq.dll`
on `PATH`. No 32-bit libpq is published for Windows; build it with vcpkg
(`vcpkg install "libpq[core]:x86-windows"`) and add its `installed/x86-windows/bin`
folder to `PATH`, as the native CI job does. For a local server, a PostgreSQL
container is enough:

```powershell
docker run --detach --name tetramaster --env POSTGRES_PASSWORD=PASSWORD --publish 127.0.0.1:5432:5432 --volume tetramaster:/var/lib/postgresql/data postgres:16
```

`--database-url` overrides `TM_DATABASE_URL`; prefer the environment variable
to keep credentials out of command history. Only `tmserver --list-accounts`
works without a database.

Current schema revision is `0003_identities`. Stop the owning server and run
`tmserver-db upgrade` when upgrading an existing database. Revision 0002 adds the
administration audit journal; 0003 adds durable authentication ownership. Neither
rewrites accounts or prizes. The server refuses an older schema. Migrations only
upgrade.

Create the database and its credentials separately. Use a direct PostgreSQL
connection or a session-pooling endpoint: transaction-pooling proxies cannot
preserve the session writer lock. Run migrations with a schema-owning role.
Use TLS and the appropriate PostgreSQL URL parameters on a remote deployment.
Do not put real credentials in committed files or command-line arguments.

Protocol listeners bind only to loopback.

## Ownership and recovery

One server process owns one database. A session advisory lock rejects a second
writer immediately; migrations use the same lock and require the server to be
stopped. Keep the database store context open until the server
has completed shutdown, including any final table cleanup:

```python
from tmserver.network.server import Server
from tmserver.storage.postgres import PostgresStateStore

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

Use [offline administration](ADMIN.md) to queue awards or event grants without
editing Python or JSON. For embedded administration, use the same transaction
boundary as the protocol handler:

```python
from tmserver.economy.claim_transactions import prize_transaction

with prize_transaction(game, account_key) as prize:
    prize.grant_award(account_key, "local-award-001", top30=500)
```

The context saves the account and award journal before publishing the award.
The ID identifies the logical award and must be stable across retries. Direct
domain-service methods remain in-memory APIs; their caller owns persistence.

## Backups

Backups are outside this project; use PostgreSQL's own tools, such as `pg_dump`.

## Tests and current limits

```powershell
$env:TM_TEST_DATABASE_URL = 'postgresql+psycopg://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/postgres'
python -B -m unittest discover -s tests -t . -p '*postgres*.py' -v
```

Tests require permission to create databases and terminate their own test
connections. Each test creates a random `tm_test_...` database and drops only
that database on cleanup. The administrative database's tables are not changed.
Without this environment variable, PostgreSQL tests are skipped.

This is the save backend for a single-process server. It is not a distributed
architecture. Full administrative
saves still visit all accounts; database I/O blocks the event loop.
Each service journal is currently one versioned JSONB document. Ordered card
slots preserve duplicate copies, but do not yet provide permanent per-copy IDs.
See [PERSISTENCE_DESIGN.md](PERSISTENCE_DESIGN.md) for those deliberate boundaries.
