# Checks and releases

The [Checks workflow](https://github.com/sruon/tmserver/actions/workflows/checks.yml)
runs on main, pull requests and manual dispatch. It checks lint and complete test
discovery, image-free tests on Windows and Linux, isolated admin/auth tests, and
transactions/migrations against a disposable PostgreSQL 16 container. Unexpected
skips fail the run. The aggregate **All checks** job fails if any required job
fails or is cancelled, and can be selected as a branch-protection requirement.

Packaging builds from fresh sources and checks exact wheel contents before
installing in a new environment. The installed base, admin and auth flows are
exercised where they do not execute original code. A
separate Windows x86 job compiles the required C++23 rules bridge and validates
its ABI without an original game image. Successful builds retain the
`tmserver-wheel` and `tmserver-native-win32` artifacts for 14 days.

The **Native gameplay** workflow runs the image-dependent `native` test lane,
8,067 frozen original-routine cases, and installed-package startup and auth checks
on 32-bit Windows. It runs only when started by hand from the Actions tab. It
downloads the game image from the `TM_ORIGINAL_IMAGE_URL` repository secret, an
HTTPS URL the maintainer controls, and checks its size and SHA-256 before use.
Secrets are not available to forks or outside contributors, so they cannot run
this workflow; run the native lane locally with your own copy instead. The image
is never a repository or release artifact. Passing Checks does not mean the
native lane passes.

`tests/native_modules.json` assigns image-dependent modules to the native lane.
The remaining portable/admin/auth/PostgreSQL lanes cannot silently skip native
failures. Collection checks ensure every maintained test module has one lane.
Locally, set `TM_ORIGINAL_IMAGE` and `TM_NATIVE_BRIDGE`, install
`".[dev,admin,auth]"` into 32-bit Python, then run
`python -m tools.check_tests native` and
`python -m unittest research.native_backend_tests`.

Two complete-match PostgreSQL tests use the separate `native-postgres` lane;
the hosted PostgreSQL job runs the other 78 tests without an image. Run the
combined lane on Windows x86 with a matching libpq and a disposable PostgreSQL
host. `python -m tools.check_tests native-postgres` creates a Docker test host;
for an already prepared disposable host, set `TM_TEST_DATABASE_URL` and add
`--worker`. This combined lane is currently verified locally, not on hosted CI.

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
does not run Native gameplay; start that by hand before tagging a release. It attaches the Python wheel, `tmserver-native-win32.zip` containing our
required bridge, and `SHA256SUMS`. Original game DLLs, private configuration,
account saves and local logs are not packaged. Nothing is deployed to a server
or uploaded to PyPI by these workflows.

For a failed release, correct the cause and rerun failed jobs, or manually run
Release packages on the same version tag. A draft remains unpublished until
successful verification. Creating a matching version tag manually uses the same
verification and publication path. Do not move an already published version tag.

Actions are pinned to commit hashes. Dependabot proposes monthly action updates
and Python dependency updates; web and PostgreSQL dependencies are grouped.

## Local checks

Use 32-bit Windows Python for the native lanes; the others also run on 64-bit
Python and Linux.

```powershell
.venv/Scripts/python -m pip install -e ".[dev,admin,auth]"
.venv/Scripts/ruff check src tests tools
.venv/Scripts/python -m tools.check_collection
.venv/Scripts/python -m tools.check_tests portable
.venv/Scripts/python -m tools.check_tests admin
.venv/Scripts/python -m tools.check_tests auth
.venv/Scripts/python -m tools.check_tests native
.venv/Scripts/python -m build --wheel
.venv/Scripts/python -m tools.check_wheel dist/tmserver-0.1.0-py3-none-any.whl --native --admin --auth
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
