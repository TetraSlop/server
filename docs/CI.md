# Checks and releases

The [Checks workflow](https://github.com/TetraSlop/server/actions/workflows/checks.yml)
runs on main, pull requests and manual dispatch. It checks lint and complete test
discovery, image-free tests on Windows and Linux, isolated admin tests, and
transactions/migrations against a disposable PostgreSQL 16 container. Unexpected
skips fail the run. The aggregate **All checks** job fails if any required job
fails or is cancelled, and can be selected as a branch-protection requirement.

Packaging checks exact wheel contents before installing in a new environment,
then starts the installed services and smokes the entry points and admin
templates. Successful builds retain the `tmserver-wheel` artifact for
14 days.

The portable lane includes `tests/game/test_rules_vectors.py`, which checks the
Python rules against the 8,067 results frozen from the client's own code in
`research/`. No lane needs game files.

## Versioning

Use conventional commit subjects (`feat:`, `fix:`, `chore:`, and breaking-change
markers). Release please maintains a version/changelog pull request against main.
Its Python strategy updates `pyproject.toml` and the release manifest together.
Review and merge that pull request to create a version tag and draft release.
There is no automatic merge.

GitHub creates approval-required Checks runs for PRs opened or updated with its
repository token. Review the release PR changes, then select **Approve workflows
to run** in its merge box. Checks then runs against the proposed merge. The bot
does not approve its own runs or create a second copy of the test suite. Tags
created with that token do not start push workflows, so release please explicitly
dispatches Release packages for its created tag. These are GitHub's documented
[workflow trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

Only the release-please workflow can write PRs; ordinary test jobs have read-only
repository permissions. GitHub Actions must be allowed to create pull requests
in the repository workflow settings. No personal token is required.

Release packages validates that the tag is exactly `v<pyproject version>`, reruns
the Checks workflow on that tag, and publishes only after every check passes. It
attaches the Python wheel and `SHA256SUMS`. Original game DLLs, private configuration,
account saves and local logs are not packaged. Nothing is deployed to a server
or uploaded to PyPI by these workflows.

For a failed release, correct the cause and rerun failed jobs, or manually run
Release packages on the same version tag. A draft remains unpublished until
successful verification. Creating a matching version tag manually uses the same
verification and publication path. Do not move an already published version tag.

Actions are pinned to commit hashes. Dependabot proposes monthly action updates
and Python dependency updates; web and PostgreSQL dependencies are grouped.

## Local checks

Every lane runs on Windows and Linux.

```powershell
.venv/Scripts/python -m pip install -e ".[dev,admin]"
.venv/Scripts/ruff check src tests tools
.venv/Scripts/python -m tools.check_collection
.venv/Scripts/python -m tools.check_tests portable
.venv/Scripts/python -m tools.check_tests admin
.venv/Scripts/python -m build --wheel
.venv/Scripts/python -m tools.check_wheel dist/tmserver-0.1.0-py3-none-any.whl --admin
```

The portable lane needs no database. It checks source import boundaries, rejects
duplicate test IDs and fails on any skipped test.

For the PostgreSQL lane, install `".[dev,postgres]"`, start Docker and run
`python -m tools.check_tests postgres`. It starts a PostgreSQL 16 container with a
random password on a loopback port, runs every database test with no skips
allowed, and removes the container and its data afterwards. No lane reads
`TM_DATABASE_URL` or `TM_TEST_DATABASE_URL` from your environment.

`tools.check_wheel` checks the wheel's exact file list and bytes against the
source tree before installing it into a new environment, so a stale file left in
`build/` fails the check instead of reaching a release; delete `build/lib` and
rebuild. It then smokes the installed entry points and extras; behaviour itself
is covered by the source test lanes.
