# Frozen client results

Results captured from the client's own battle, growth, target and computer-move
routines, which the Python rules in `src/tmserver/game` must reproduce exactly.
`tests/game/test_rules_vectors.py` regenerates every case and checks them.

- `native_vectors.json` stores result and draw digests and small explicit vectors.
  Growth was recorded at 64-bit x87 precision (600 cases at 53 bits).
- `native_special_vectors.json` stores special-board and three-player placement
  vectors.

The protocol itself is documented in
[TetraPackets](https://github.com/TetraSlop/packets).
