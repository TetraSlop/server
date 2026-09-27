# Offline admin PostgreSQL validation

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
