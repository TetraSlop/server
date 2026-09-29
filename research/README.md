# Original-client research

The supported executable research path is the native Win32 bridge described in
[the native rules guide](../docs/ORIGINAL_RULES_PROTOTYPE.md). It uses the original
external image directly, without an emulator or game launch.

- `native_backend_tests.py` checks native guards, lifecycle and frozen results.
- `native_primitive_cases.py` generates deterministic regression inputs.
- `native_vectors.json` stores result/draw digests and small explicit vectors.
- `native_special_vectors.json` stores compact special-board and three-player
  placement vectors captured before the duplicated Python scorer was removed.
- `tools/probe_original_rules.py` runs the native corpus and reports timings.

`migration.json` preserves the source location, module hashes and the inventory
of research methods omitted from the portable suite. The former archived
`research/tests/` copy, executable emulator probes and fixture-generation scripts
were retired; their source remains in Git history. Frozen protocol evidence stays
in `tests/fixtures/`, and the maintained tests stay in `tests/`.

`migration_scripts/` contains one-time refactoring recipes for review, not
supported commands. Historical protocol and implementation notes remain in
`docs/research/`; references there to emulator executions describe past evidence,
not an installable or supported backend. Original binaries, traffic logs and
saves remain external.
