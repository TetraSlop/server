# Offline admin PostgreSQL validation

## Repeated isolated checkpoint, 2026-09-27

The six modules listed below, including `tests.test_admin_postgres`, pass all
**59 tests in 55.163 seconds**, with no skips, errors or failures, against an
archive of exact revision `2a6bf1cc25e4ad319f024b90bb16dfeb8dec7894`.
All 467 archived file hashes remained unchanged. This checkpoint predates the
subsequent event-admission ownership correction; its focused dispatcher checks
are recorded separately in [VALIDATION.md](../VALIDATION.md).

The run reused cached Docker 29.8.1 binaries and PostgreSQL 16.15 without
downloads or Docker Desktop. Its private daemon, socket, data and container
lived under `/tmp/tm-pg-check.SmxFzX`, with loopback port 49879 verified unused
before startup. The container ID, ownership label and bind path were checked;
Windows and container connections returned the same database system identifier,
`7690327211016986659`. Each test used its own disposable database. Only the
three standard databases remained afterward.

Cleanup verified removal of the owned container, private daemon, namespace mount
and temporary root, and absence of Linux and Windows listeners on the test port.
Cached binaries and images were retained. No existing database or live save was
used. Evidence is under ignored `results/postgres-validation-2a6bf1c-20260927/`:
`tests.log`, `test-summary.json`, `source-manifest.json`, `cleanup.log` and
`cleanup-summary.json`.

## Earlier administration checkpoint

On 2026-09-27, the three tests in `tests/test_admin_postgres.py` passed against
PostgreSQL 16.15 in 1.826 seconds. They cover:

- Prize awards across new store connections, replay with changed amounts, and
  replay after redemption, without double crediting.
- Event grants, exact-content replay, eligibility changes, event configuration
  mismatch rejection, and preservation of accounts and weekly rating state.
- Refusal while another writer owns the database, no success output or saved
  mutation on refusal, and successful administration after that writer closes.

The existing 56 PostgreSQL tests also passed in 55.382 seconds:

```text
tests.test_postgres_store
tests.test_postgres_protocol
tests.test_commerce_postgres
tests.test_match_transactions_postgres
tests.test_claim_transactions_postgres
```

The new tests use the existing `DatabaseFixture`, which creates and drops an
individual temporary database for each test. They remain optional unless
`TM_TEST_DATABASE_URL` points to a PostgreSQL endpoint suitable for disposable
test databases.

## Isolation and cleanup

The validation used cached Docker binaries and the cached PostgreSQL 16 Alpine
image, without downloading packages or starting Docker Desktop. A new private
Docker daemon, socket, storage directory, and container were created beneath
the new WSL temporary directory `/tmp/tm-admin-pg.ICMbGl`. The daemon disabled
bridge creation and iptables changes. PostgreSQL listened only on
`127.0.0.1:49785`, a port checked unused before launch.

Before tests, the container ID and ownership label, bind mount from the new
temporary directory, PostgreSQL `data_directory`, port, and listen address were
verified. The Windows test connection returned the same database system
identifier as the owned container (`7690168989240492068`). No existing database
or user save was used.

After tests, only the three standard PostgreSQL databases remained. The owned
container was stopped and removed, its private daemon was terminated and its
process absence verified, and its remaining private namespace bind mount was
unmounted. With no mounts remaining beneath the verified temporary root, that
root was removed. The test port had no remaining Windows listener. Cached
binaries and images were left unchanged.

These checks validate database persistence and command integration; they do
not exercise the native game UI or supply historical retail award policies.
