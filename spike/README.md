Phase 1 spike: the Python rules removed in e3192ab, restored unchanged under
`oldrules/` (only the 24-bit precision guard relaxed), run against the frozen vectors.

- `dump_oracle.py` (32-bit Python) dumps per-case native results, including growth
  at 24 bits through a bridge rebuilt from `bridge24.cpp` (precision 24 -> CW 0x007F,
  with engine.cpp's valid() accepting 24).
- `run_old.py oracle.json` compares every case.
- `run_lane.py` runs the native test lane with `oldbackend.OldRules` as the rules.
