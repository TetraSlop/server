# VS. Rating producers and hosted preservation

There is an original **offline** producer for VS. Rating changes. It generates
random positive or negative components from the result's ordering; it is
separate from the 32-result average-rating formula. Hosted result presentation
bypasses this generator and consumes server-provided components instead.
Nothing here establishes that the retired server used the offline algorithm.

The current neutral hosted policy, `T=0|0|count`, preserves the imported
VS. Rating total exactly, including unsigned boundary values. No runtime change
is required by these findings.

## Distinct fields

| Value | Player memory | Saved profile | Result message |
| --- | --- | --- | --- |
| VS. Rating total | +B0, global`102B6490` | +128 | T0/T1 are temporary changes, not this total |
| VS. Player Games | +B8, low WORD | +130, WORD | T2 is the final count |
| Average rating | +4 | +30 | D2 is the final rating |
| Memorable Win text | +128 | +7D | RN is the string |
| Prize Points balance | +B4 | +134 | Neither T0 nor T1 |

The external `protocol_map/RESULT_STAT_LABELS.md` already proves VS. Rating's
display in hundredths: total12345 displays as123.45. Its existing
`vs_rating_components_vectors.py` proves signed component consumption. This
note adds the producer and complete presentation controls rather than treating
the previously known consumers as a formula.

## New offline producer evidence

After result setup `100CBD80`, player+1A5 contains the unique result ordering.
The tested normal score cases order higher scores first, breaking equal scores
by server seat. It is distinct from player+1A1, the class0..4 used by the
average-rating formula. A tied participant can be first in this ordering.

Original result update `100C79C0`, state2F, reaches `100C8033` after its elapsed
time threshold. `100C804E..100C8055` checks player+EA: zero enters the offline
generator `100C8102..100C8151`; nonzero skips it. With original CRT output
values r0/r1 in0..32767:

| Local result order | Generated T0 | Generated T1 | Random calls |
| --- | --- | --- | --- |
| 0 | r0 %1000 | r1 %1000 | 2 |
| 1 or2 | -(r0 %1500) | 0 | 1 |

The fields are signed WORDs at game+1D8/+1DA. Their generation does not yet
change player+B0. Later rating presentation applies them with the previously
proved signed-negative clamp.

The original formatter `100CDA80` is consequential: if local result class is
at least2, `100CDB47` clears T1 before it can be applied. The probe executes
this entire formatter and only replaces its two final drawing helpers. A
probe that replaces the whole formatter would miss this rule.

For example, supplied draws1999/2999 yield +999/+999 for a sole first-place
winner. In a two-player full tie, server seat0 gets order0 and generated
+999/+999, but presentation clears T1, leaving +999. Seat1 gets order1 and
-499/0. This asymmetry is original offline behavior, not evidence of a fair
or historical hosted rating policy.

The constructor's exact store at `1010104C` initializes player+B0 to1,000,000
(display10000.00). This is an in-memory default; a profile import overwrites it.
It does not establish the original server's starting rating or justify changing
the replacement server's account defaults.

## Hosted and persistence boundaries

The original profile importer copies profile+128 into player+B0 at
`1010121D..10101222`; the scalar export tail copies it back at
`10101620..10101627`. Separately, the already mapped event replies EInit,
EcmInit and Event assign a received `/RT=` total at `10105716`, `10105866`
and `10105B77`. Those are authoritative value assignments, not calculations.

The ResultData receiver resets its temporary T0/T1 fields, then imports supplied
T at `1010436A..101043CC`. T2 updates the count at player+B8. With player+EA
nonzero and game+18A equal2, rating presentation takes the received-components
path at `100C8088`, without calling the offline random generator.

Twenty controls execute an actual profile import, actual ResultData receiver,
full `100C79C0` state progression and scalar profile export. They cover hosted
modes1/2, winning/tied result classes, and imported totals0,1,0x7FFFFFFF,
0x80000000,0xFFFFFFFF. The message's local T is `0|0|65535`.

All pass through states2F→30→31→32→33→35 (Ready wait), with the original
`100CDA80` formatter intact. Every VS. Rating bit survives, including totals
whose high bit is set. T2 becomes65535 independently, and Prize Points remain
unchanged. Zero T0 skips its addition/clamp branch; nonpositive T1 skips its
addition/clamp branch. Thus neutral components do not accidentally normalize
an existing high unsigned total.

## Reproduction and scope

```powershell
python -B research/probe_vs_rating_sources.py --research-dir '<scratchpad>/tetramaster'
```

The external directory supplies `protocol_map/` and reconstructed
`TM_emulated_mapped.dll`, SHA-256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
This is the TM code image, not a decoded data asset. The probe prints all
inputs, draw counts, components, states and totals. Retained ignored output:
`results/vs-rating-sources-original.json`.

There are **93 passing cases**:72 complete offline result/presentation cases,
20 neutral hosted import/receive/presentation/export cases, and one isolated
constructor store. Synthetic scores, seats and clock values are used. Supplied
random outputs verify the original arithmetic and number of draws; they do not
reconstruct historical hosted seeds or scheduling. No native application,
desktop, network, original asset write or live save is involved.

The [current rating policy](RATING_POLICY_NOTES.md) can continue updating the
separate average rating while preserving VS. Rating and sending neutral T0/T1.
Using the recovered random offline producer on a server would require an
explicit local policy decision; it is not implied by this research.
