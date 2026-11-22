# Native rules checks

These files check the native rules bridge against frozen results. They need the
game image and 32-bit Windows Python; see the [native rules guide](../docs/NATIVE_RULES.md).

- `native_backend_tests.py` checks native guards, lifecycle and frozen results.
- `native_primitive_cases.py` generates deterministic regression inputs.
- `native_vectors.json` stores result and draw digests and small explicit vectors.
- `native_special_vectors.json` stores special-board and three-player placement
  vectors.
- `probe_original_rules.py` runs the corpus and reports timings.

The protocol itself is documented in
[TetraPackets](https://github.com/sruon/TetraPackets).
