# Memorable Win sources and offline boundary

The original offline result calculator does **not** select or replace the
Memorable Win string in the tested ordinary results. The known producers are
the saved profile and the hosted ResultData `RN` field. This investigation finds
no RN selection formula to port and does not establish the retired server's
choice of which victory to remember.

The earlier external `protocol_map/RESULT_STAT_LABELS.md` and `RN_DISPLAY.md`
prove the label: Play Data reads player+128 (`102B6508`), displaying the text
under Playdat.BIN entry84, **Memorable Win**, or entry85, `-`, when empty.
Changing numeric rating does not change that display. RN occupies the 32-byte
profile field at +7D; usable text is bounded to 31 bytes plus its terminator.

## New original-instruction evidence

`research/probe_memorable_win_sources.py` adds a memory-write watch over all
32 bytes of player+128 while executing original code. A nonempty string with
sentinel bytes after its terminator detects both visible replacement and any
otherwise hidden writes. There are **458 passing cases**:

- **430 complete offline result calculations** at `100CBD80`: every partition
  of ten cards between two players or fifteen cards between three players, at
  each local-seat rotation. Wins, losses, ties and perfect outcomes leave every
  RN byte unchanged and issue no writes to that region. Positive controls show
  the original classification, rating-ring update and sequence advance ran.
- **10 online result controls**, modes1/2 at every two-/three-player seat,
  also preserve RN and skip the offline rating update as expected.
- **3 profile imports**, executing `101011FA..1010130C`, replace RN with empty,
  ordinary or 31-byte saved text. The source is `10294C4D` (profile+7D), copied
  into player+128 through the original call at `101012B6`.
- **15 ResultData receiver cases**, at all five seat positions: an omitted
  local `RN` preserves the old bytes; empty local `RN` clears the string; a
  supplied local string replaces it. Nonlocal strings do not replace the
  local player's value. The write watch records the original byte-copy
  instruction `10104444`, once per supplied byte and terminator.

The static review agrees with those traced paths: the known direct references
to the global RN buffer are its display, and the player-relative producers are
profile import at `101012A4..101012B6` and ResultData at `1010443B..10104449`.
This is scoped instruction evidence, not a proof that every possible indirect
writer or other client mode has been exhausted. Complete later result scenes,
untraced special modes and historical online scheduling are outside this probe.

## Reproduction and current policy

Use the research Python environment with the external reconstructed code image
and `protocol_map` Unicorn harness:

```powershell
python -B research/probe_memorable_win_sources.py --research-dir '<scratchpad>/tetramaster'
```

The script requires `TM_emulated_mapped.dll` SHA-256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
This is the reconstructed TM **code** image, not a decoded data catalogue.

The probe prints its code-image SHA-256, all inputs/results and observed writer
addresses. The retained ignored output is
`results/memorable-win-sources-original.json`. It seeds synthetic player,
collection-independent result and transport state; only the existing harness's
presentation/audio/runtime boundaries are intercepted. No native application,
desktop rendering, networking, original asset edits or live saves are involved.

The current [rating policy](RATING_POLICY_NOTES.md) correctly preserves RN even
when it adopts the proved offline rating arithmetic. Neither that ring nor the
offline reward calculation supplies a Memorable Win selection rule. No runtime
change or invented automatic award follows from this evidence.
