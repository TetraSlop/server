# Ranking dates and large-score display review

Automatic weekly rankings now omit native date labels that cannot accurately
describe the local accounting periods. The saved weekly journal, current/previous
scores and manual historical timestamp option remain unchanged. This note records
the original-code evidence for that presentation correction and a separate
large-score display limitation. The probe runs original x86 instructions in
isolated Unicorn memory, never the installed application.

From the tmserver repository root, reproduce with global Python containing
Unicorn and Capstone:

```powershell
python -B research/probe_ranking_display.py --research-dir 'C:/Users/sruon/AppData/Local/Temp/claude/C--Users-sruon-orca-workspaces-lsb-friend-list/f1bb5c9f-1aa8-497e-8b3d-3687f6740740/scratchpad/tetramaster'
```

The script imports the existing external `protocol_map` harness, verifies both
image hashes, constructs synthetic accounts and commits results through the
production rating policy. Its default mode requires actual production metadata
to omit dates. The period start is read independently from the policy, and old
week-start metadata is reconstructed only as an explicitly labeled comparison.
The output is `results/ranking-display-zero-date-original.json`: **58 passing
cases**, comprising 40 score boundaries, nine old date labels and nine production
date omissions. This default invocation verifies the corrected production
metadata. The verification transcript is `results/ranking-display-original.log`.

To reproduce against the pre-fix source, append `--legacy-baseline --output
results/ranking-display-legacy-baseline-original.json` to the invocation. This
mode instead requires the old timestamp from production and labels the zero-date
case as a candidate. The same 58 baseline cases also passed before the runtime
correction; the final evidence above uses the default mode without that flag.

## Confirmed date mismatch and presentation correction

The fixture commits prize 25 during the week beginning January 1, 2024, advances
to January 8, and commits prize 99. The pre-fix implementation emits January 8 in
`b/g/TM0RkData+C`, current-week prize 99 for RR1, and the frozen January 1-week
rating for RR3/4. The original client produces these labels in locale selector 1:

| Category | Original label with pre-fix metadata |
|---|---|
|RR1 Weekly Total|`Tally Period: 12/25/2023 - 12/31/2023`|
|RR3 Top30|`Next update: 01/08/2024  0:00 PST`|
|RR4 Best Rookies|`Next update: 01/08/2024  0:00 PST`|

Locale selectors 0/2 produce the same dates in different order. The metadata
import is original `101756E3..10175724`; date drawing is original
`10176A13..10176CFA`. RR1 subtracts 14 days and 8 days at `10176AA3/10176AC4`.
RR3/4 pass the timestamp unchanged at `10176BE3..10176BF8`. These behaviors were
already documented externally in `FILE_COVERAGE.md`; the new evidence tests their
interaction with the implemented local weekly policy.

The corrected production service emits zero at metadata+C in automatic weekly
mode. Original `10176A60..10176A6D` suppresses the entire date branch when the unsigned value is
at most `0x38640900`. All nine zero-date cases produce **no calendar calls and no
date-label drawing calls**. Their imported denominators, row counts, complete
projected row bytes, names, scores, ranks and source-file bytes remain identical
to the corresponding nonzero cases. The probe separately compares all `0x2C`
bytes of each projected row between the zero/nonzero cases and before/after date
processing, alongside its named-field assertions. The production economy and
weekly snapshots remain unchanged.

The correction retains the journal's real period start and all current/previous
accounting. Manually supplied historical `update_time` is unchanged when automatic
weekly tracking is disabled. Omitting a misleading presentation field does not
establish a historical schedule or change the local weekly policy.

Changing the nonzero timestamp alone cannot make both labels truthful: adding
seven days fixes the next-update label but makes Weekly Total describe the
previous week; adding 14 days fixes the current-week tally label but puts the
next-update label two weeks ahead. An eventual wrapper presentation change could
support these local periods explicitly; it is outside this correction.

## Calendar execution and timezone limits

The probe executes original Core Common692 (`1004CEF0`), its bit 31 mask
(`1004CF5E`), and the original CRT calendar conversion. Only the CRT per-thread
calendar buffer and timezone initialization/getters are replaced, fixing the
fixture to UTC with no daylight adjustment. The TM date formatter and its CRT
`sprintf` execute unchanged. Python does not supply calendar values or label
text. Final drawing calls are captured.

The original format contains literal `PST`, including when the synthetic calendar
is UTC. The output therefore proves original label construction under a stated
timezone fixture; it does not establish the installed timezone, historical
server epoch policy, or a correct PST conversion. Common692's bit 31 mask also
means arbitrary unsigned timestamps are not interpreted as a full-width future
Unix date. No new timestamp policy is inferred here.

## Large scores: retain values, defer presentation

The original file projection `101752A8..101754F8` retains every DWORD exactly.
Later score drawing `1017663A..1017676C` uses signed comparisons and signed decimal
formatting. Prize totals use helper `10174CE0`; ratings use hundredths formatting.

| Wire value | Grand/Weekly Total display | Rating/Top30/Rookies display |
|---:|---|---|
|`0x80000000`|`-2147483648`|`0.-2147483648`|
|`0xFFFFFFFF`|`-1`|`0.-1`|

The 40 cases cover all five ranking categories with zero, hundredth boundaries,
the existing positive display caps, signed maximum, and both high-bit examples.
These are original renderer limitations, not grounds to clamp or rewrite the
saved unsigned scores, change tie comparison, or alter ranking order. Preserve
the current values and retain this evidence for a later wrapper review.

## Image identity

- Reconstructed mapped TM code image SHA256:
  `422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
- Original Viewer Core image SHA256:
  `f39e232820e072189becc179bcec292a5b73563dfef94f7a9b6446299166adfb`.

The probe writes only its requested JSON output, normally under ignored
`results/`. No native game, desktop, external connection or live save participated.
