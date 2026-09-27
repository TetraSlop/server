# Persistence design for the Python server

Status: PostgreSQL backend implemented alongside the existing JSON backend.
See [DATABASE.md](DATABASE.md) for setup, import/export and verification.

## The boundary

The game domain remains ordinary Python dataclasses and services. StateStore has
four operations: load, save a full snapshot, save selected accounts, and save
selected accounts with named service journals.
Commerce coordinates ordinary purchases, trades and delayed trade deck edits.
It stages just the affected accounts, calls the scoped save, then publishes their
live state. The store owns SQL transactions; domain services own economic rules
and native protocol phases. The ordinary-table match_transaction context stages
accounts, match settlement, result rewards, ratings and returned-card recovery.
Concrete auction, prize and event transaction contexts stage the remaining
economic services. Character-profile edits stage their immutable record map,
while account options and settings use the one-account commerce context.
Account creation publishes both its account and identity-cache entry only after
saving. Live request handlers no longer call Server.persist.

SQLAlchemy Core expresses four concrete tables and short transactions. Alembic
owns schema changes. An ORM object graph, repository hierarchy and another set of
Pydantic game models would duplicate existing objects without clarifying this
boundary. Import uses the existing domain restore validators.

## What is stored

| Table | Content |
|---|---|
| accounts | Native identity, player ID, name, guild, balance, deck names, preserved profile bytes and session counter |
| cards | Ordered eight-field card copies, keyed by account and inventory slot |
| service_state | One versioned JSONB document per existing durable service |
| state_meta | Schema-independent snapshot version, committed revision and optional import checksum |

Alembic also maintains its own schema revision table.

The nine service documents contain auctions and their escrow/claims, Prize
Center awards, result rewards, match settlement/recovery receipts, rating history,
character profiles, event membership/progress, event grants and event outcomes.
They are saved in the same transaction as affected inventories and balances.
This preserves the existing recovery semantics without maintaining two
authoritative economies.

Native identities use NUMERIC(20,0), covering the full unsigned 64-bit range.
Native names and profile templates use binary columns. Profiles are still
assembled by the existing codec: mapped fields come from domain state, and
unmapped template bytes are preserved. Duplicate cards retain their multiplicity
and order.

Permanent card-copy IDs are deferred. Today's domain represents cards as value
objects and changes their tuples during growth; identifying copies by matching
attributes would invent continuity and mishandle identical cards. Adding stable
IDs must begin in the domain, then follow each transfer through the protocol.

The service documents are intentionally an incremental step. A changed auction
or match journal is rewritten as one JSONB value. Normalizing claims and receipts
can happen when those operations acquire explicit database-owned transactions;
there is no unused speculative schema for them today.

## Commit and failure behavior

Commerce operations run against isolated copies of one shop account or two
trading accounts. The staging context preserves reservation identity and clones
the random generator. After validation, PostgreSQL compares and writes only the
specified accounts. Successful commit publishes the staged accounts and random
state; an exception discards them. A failed commit therefore leaves the live
inventories unchanged.

Trade negotiation, M4 reconciliation and the first Get are transient. Only final
Get calls the trade transaction; delayed deck edits have their own one-account
commit. Buy stages the offer, Get commits the purchase, and replay behavior stays
native. Scoped JSON commits still replace the whole JSON file, preserving its
other accounts and journals from the previous complete save.

Ordinary match admission, results, prize transfer, Continue/late deck edits and
disconnect recovery stage affected accounts and journals together. Round rows
for participating accounts and their mutable result editors are copied; unrelated
table capabilities and active auction collections retain their identity. Rating
policies replace validated history rather than mutating a shared history object.
Returned-card recovery stages only the append-related auction fields. After a
successful save, these fields publish back into the existing service objects,
keeping adapter references stable. Table reservation updates and result/prize
acknowledgements follow the commit. Failed-writer cleanup releases capabilities
without trying to refund or recover potentially committed changes in memory.

The four match journal documents are still serialized in full. This removes the
all-account snapshot from match settlement, but is not yet bounded work per
match as the journal history grows. The transient board/choice state is also not
rolled back; a failed save closes the session and requires startup recovery.

Auctions stage listings, bids, pending claims and the current collection editor.
An outbid creates the previous bidder's refund receipt without changing that
account; collection credits the account and consumes its receipt together.
Expiry stages listing closure and claims without touching accounts. Prize Center
stages awards, points, purchases and its random generator. Event collection
stages grant money/card offsets, account changes and admission state together.
Event results commit progress and outcome receipts before acknowledgement;
borrowed-hand event deck edits have a separate scoped account commit.

These contexts preserve live service references and unrelated collection tokens.
Only mutable state belonging to the selected accounts is copied deeply; immutable
rows and map entries can be shared. Journals still serialize as whole documents.
Native confirmation phases, catalogue selection and reward formulas are unchanged.

Server.persist remains available for startup recovery before listeners open and
explicit trusted administration. Direct domain calls still need a transaction
context or a joint save; domain services do not silently perform I/O. A full save:

1. Checks snapshot shape and compares it with the last committed snapshot.
2. Starts one short SQLAlchemy transaction and advances the expected revision.
3. Writes changed accounts and their changed ordered inventories.
4. Writes changed service documents, including settlement/claim receipts.
5. Commits before returning to the protocol handler.

A no-op verifies the database revision without writing rows. Full saves still
build and visit the whole in-memory snapshot. Scoped commerce saves never scan
unrelated accounts or journals. The writer's baseline advances only after
COMMIT succeeds.

On an error, the writer becomes unusable. The shared storage error handler stops admission and
closes clients; success is not published. A new process reloads committed state
and runs the existing startup recovery. If the connection fails after COMMIT but
before acknowledgement, this also handles the ambiguous outcome without
repeating a debit or granting from the old in-memory state.

The legacy protocol has distinct confirmation phases. In particular, trade M4
is not its final durable Get acceptance. This storage change preserves those
phases and their existing disconnect/resynchronization behavior.

## Writer ownership

JSON keeps a SHA-256 fingerprint of the last file it read or committed. A later
read or save rejects a changed or missing file instead of silently rebasing live
accounts. A save compares that fingerprint while holding a nonblocking OS lock
on a stable sidecar, then writes a unique temporary file, flushes it and replaces
the target. The containing directory is also flushed on POSIX. The file format
does not change. Semantically unchanged snapshots skip replacement but still
check for outside changes.

The empty `.lock` sidecar stays in place; deleting it during service could let
writers lock different files. The lock is held only during a commit, and the OS
releases it on descriptor close or process exit. This is optimistic protection
against competing local writers, not lifetime single-server ownership. External
editors do not obey the lock; editing or replacing a live save remains unsupported.
Network filesystems and hard-link aliases are outside this local JSON boundary.
Errors permanently fail the store, including ambiguous replacement outcomes.
A fresh process reloads the actual committed file and applies normal recovery.

A session-scoped PostgreSQL advisory lock allows one application writer per
database. Its dedicated physical connection uses no reusable pool. A second
server fails fast. Offline migrations take the corresponding transaction-scoped
lock, and import/export acquire the normal writer lock. The revision check
provides an additional stale-state guard.

Locks are cooperative: direct SQL edits are unsupported while the server is
running. Transaction-pooling proxies are unsupported because ownership must
stay on the same PostgreSQL session. A broken writer never transparently
reconnects with its old in-memory state.

This remains synchronous and single-process. Staging contexts must not span an
await: their reservations and random generator are in-process state. Thousands
of players have not been load tested. Multi-writer concurrency still needs
database-owned account ownership and operation receipts. Only then should independent tasks use separate
AsyncSessions or multiple processes own accounts and tables.

## Import and recovery

The offline importer reads a save without modifying it, validates present
sections using existing domain readers, and requires an empty migrated database.
It retains optional-section absence in old saves, writes one transaction, records
the source hash, and compares generated native profile bytes after a database
read. Event data requires matching event configuration.

It does not run match recovery during import. Server startup performs that
recovery and saves it before opening protocol listeners. Interrupted stakes
refund once; paid results remain paid; unclaimed cards retain the returned-card
collection path. An active board, socket, encryption stream, trade consent and
unaccepted shop offer remain transient.

An offline JSON export permits a deliberate switch back to the existing backend.
It refuses file overwrite. The pre-import save ceases to be a valid rollback
point once new database transactions have been accepted.

## Verification

Real PostgreSQL tests cover schema/model agreement, repeatable migrations,
duplicate-writer rejection, no-op and per-account writes, rollback after account
and journal failures, lost connections, lost COMMIT acknowledgement, unsigned
identities, duplicate cards, unknown profile bytes, import/export and repeated
startup recovery.

JSON tests exercise real files and separate processes on Windows and Linux:
missing/replaced saves, stale writers, simultaneous commit attempts, process
death, flush/replace failures, no-op writes and lost replacement acknowledgements.
Encrypted client tests verify storage conflicts close admission without a success
reply. Auction recovery verifies an ambiguously committed payment is not repeated.

Encrypted-socket tests exercise purchases and duplicate Buy/Get behavior,
two-account trades, wagered games with prizes and ratings, auctions and
collection, and partial event grants. The JSON backend retains its existing
failure and protocol regression tests. These are headless tests; they do not
launch or control the native game.

Production hosting still needs an operational backup and tested restore policy,
plus load testing and observation of event-loop stalls. Schema migrations and
transactional durability do not replace backups.

## References

- [SQLAlchemy transaction contexts](https://docs.sqlalchemy.org/en/20/core/connections.html#using-transactions)
- [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [PostgreSQL advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)
- [PostgreSQL backups](https://www.postgresql.org/docs/current/backup.html)
- [Python Windows file locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking)
- [Python POSIX file locking](https://docs.python.org/3/library/fcntl.html#fcntl.flock)
