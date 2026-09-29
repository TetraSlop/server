# Checks and releases

The [Checks workflow](https://github.com/sruon/tmserver/actions/workflows/checks.yml)
runs on main, pull requests and manual dispatch. It checks lint and complete test
discovery, portable tests on Windows and Linux, isolated admin/auth tests, and
transactions/migrations against a disposable PostgreSQL 16 container. Unexpected
skips fail the run. The aggregate **All checks** job fails if any required job
fails or is cancelled, and can be selected as a branch-protection requirement.

Packaging builds from fresh sources and checks exact wheel contents before
installing in a new environment. The installed base, admin and auth flows are
exercised, including automatic account creation and WebSocket revocation. A
separate Windows x86 job compiles the optional C++23 rules bridge and validates
its ABI without an original game image. Successful builds retain the
`tmserver-wheel` and `tmserver-native-win32` artifacts for 14 days.

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
in the repository workflow settings. No personal token or additional secrets
are required by this setup.

Release packages validates that the tag is exactly `v<pyproject version>`, reruns
the complete Checks workflow on that tag, and publishes only after every check
passes. It attaches the Python wheel, `tmserver-native-win32.zip` containing our
optional bridge, and `SHA256SUMS`. Original game DLLs, private configuration,
account saves and local logs are not packaged. Nothing is deployed to a server
or uploaded to PyPI by these workflows.

For a failed release, correct the cause and rerun failed jobs, or manually run
Release packages on the same version tag. A draft remains unpublished until
successful verification. Creating a matching version tag manually uses the same
verification and publication path. Do not move an already published version tag.

Actions are pinned to commit hashes. Dependabot proposes monthly action updates
and Python dependency updates; web and PostgreSQL dependencies are grouped.

## Local checks

Use the commands in the [development guide](../README.md#development). Additional
lanes are `python -m tools.check_tests auth`, `admin`, and `postgres`, after
installing their corresponding extras. PostgreSQL tests require Docker and create
only disposable databases. Original-DLL oracle research stays outside portable
CI because the game image is not part of this repository.
