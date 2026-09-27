# Original-client research

`tests/` preserves the original scratch-project test sources byte for byte. Some
execute recovered x86 instructions through Unicorn and require the original
client binaries and the external `protocol_map` harness. These sources are an
archive, not part of this package's portable test runner. Run them in their
original environment when extending the executable-derived contracts.

`migration.json` records the source location, hashes of the runtime modules and
the test methods omitted from the portable suite. `research_definitions` is the
initial automated extraction log; `archived_test_methods_not_in_portable_suite`
is the final method-level inventory. Frozen results from that research are in
`tests/fixtures/protocol/` at the repository root and are tested without Unicorn.
The normal suite still exercises encrypted TCP, domain rules and persistence.

`migration_scripts/` contains one-time refactoring recipes for review. They are
not supported commands and must not be rerun against the finished package.
Historical protocol and implementation notes are in `docs/research/`.
Native executable binaries, traffic logs and saves are intentionally external.
