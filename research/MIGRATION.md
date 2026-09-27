# Migration review

The package was extracted from the scratch server recorded in `migration.json`.
Original runtime hashes and complete original test sources are retained here.

Runtime changes:

- Qualify internal imports with `tmserver` and use a standard `src` package.
- Package the catalogue, profile contract and command dictionary; load them
  through `importlib.resources` instead of adjacent research directories.
- Provide `tmserver`, `python -m tmserver` and `tmserver-db` entry points. Remove
  the older game-listener-only script entry point. Default mutable output to
  the working directory's `var/` folder.
- Update database error hints to the packaged `tmserver-db upgrade` command.
- Move explicit copy/publish rules into each owning service. Replace the
  generic private-field publisher in transaction coordinators with named
  `stage_state`/`publish_state` operations. Match returned-card recovery has a
  narrower `stage_returns`/`publish_returns` boundary.
- Apply consistent formatting and remove unused imports/locals. The unused
  assignment in `EventRooms.bind_match_table` still calls the same validator.

A function-body AST comparison, ignoring import qualification/order and
docstrings, found changes only in transaction coordinators, `Catalog.__init__`,
the unused assignment above, and startup help/output. Database error hints were
also updated to the new CLI name. New staging methods and
entry-point wrappers were reviewed separately. Native rule, AI, combat and
reward formula bodies were retained.

Portable tests use frozen original-client vectors. Tests requiring executable
emulation remain in the original-source archive, listed in the manifest; they
are not silently counted as portable coverage. Two additional fixtures preserve
the original special-AI choice/RNG expectation and 37 authenticated application
commands through card selection from the recorded CPU match. Their source
hashes/provenance are retained with the fixtures.

A follow-up audit on 2026-09-27 removed three empty portable test modules for
admission messages, table eligibility and table passwords. Their original
instruction tests remain in this archive. The NPC module now restores the
asset-independent selector/name/guild API check that was excluded with its
native-dependent class, plus name-visibility boundaries. Actual NPC generation
and RNG comparisons remain covered by the frozen batch fixtures. This cleanup
does not promote the archived instruction tests to portable coverage.

The old scratch server, launcher, live save and separate loader repository were
not migrated in place. This package needs an explicit later launcher switch.
