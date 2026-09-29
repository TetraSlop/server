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

The newer `native_backend_tests.py` exercises the optional Win32 original-code
bridge with the external mapped image, including complete hybrid match replays.
`native_primitive_oracle_tests.py` separately compares growth and target choice
against the original instruction harness; only that research oracle needs
Unicorn. See [the native rules guide](../docs/ORIGINAL_RULES_PROTOTYPE.md) for
commands, supported routines and evidence limits. Neither the wheel nor CI
contains the original executable image.

`tests/events/test_event_notifications.py` uses a frozen original-consumer
capture in `tests/fixtures/event_notifications.json`. It validates notification
layout and client acceptance without an emulator or a running native game;
it does not establish the historical schedule or reward policy.

The [native chat name audit](../docs/research/CHAT_NAME_BYTES.md) records why chat
must retain non-ASCII name bytes. Its small original-consumer fixture and encrypted
reconnect checks remain portable; glyph rendering was not tested.
