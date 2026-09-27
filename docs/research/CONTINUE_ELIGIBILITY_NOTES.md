# Original Continue eligibility

The normal client does not send a successful rematch vote when its current
collection has fewer than five cards or its balance cannot cover the next wager.
The Computer menu's change-setup choice deliberately permits insufficient money
so the player can choose another setup. Both choices still require five cards.

`research/probe_continue_eligibility.py` executes the original x86 instructions
against synthetic player/collection data in the existing external Unicorn harness.
It checks 14 cases plus the explicit-leave sender, with no native application,
network, original asset write or live save. Its output includes the reconstructed
TM code image SHA-256, inputs, resulting scene state, dialog text indices and exact
outgoing packets. Run with the global research Python environment:

```powershell
python -B research/probe_continue_eligibility.py --research-dir '<scratchpad>/tetramaster'
```

The external directory must contain `TM_emulated_mapped.dll` and `protocol_map/`.
The recorded run is `results/continue-eligibility-original.json`; this ignored
output is reproducible from the checked-in probe and original external image.

## Same settings: Ans1 / Conf0

Both constructors, `1010BC4B..1010BC77` (human table) and
`1010BF5C..1010BF88` (Computer), double the wager for `du=1`, then cap it at 2000.
This happens before the choice and does not debit money. See
[the monetary evidence](MATCH_PRIZES_NOTES.md) for the later positive-rematch
debit and the broader original arithmetic vectors.

The yes branch `1010D191..1010D298` compares player balance at `102B64A8` with
the updated wager at `102B64BC`, then invokes the actual owned-card counter
`100B8B30(collection=102B6F10, slots=0x406)`. The counter counts card-type WORDs
at entry+6 below the loaded catalog limit. The result is cached at `102B64CC`.
The probe supplies five valid entries or four, leaving remaining slots invalid.

Money short, cards short, and both short select distinct dialog entries 91, 54, 92
through `10234D33 + failure_bits`. The original sets scene state 2 and emits
**no packet**. It does not substitute an automatic no vote at this click.
These are dialog indices, not asserted translations of the resource text.

With at least five cards and balance **equal to or greater than** the new stake,
the sender `10106E30` is called at `1010D28C`. For local seat 0 and privacy 0:

```text
GTM0G43001100@Continue=/Ans=1/PR=0/Conf=0
```

The scene moves to state 6, waiting for Continue11. Cases prove the doubled
stake 400 boundary at 399/400 and the capped stake 2000 boundary at 1999/2000.
Zero wager still requires five cards. The cached balance is unchanged by all
choice cases.

## Computer change setup: Ans1 / Conf1

`1010D2AB..1010D393` counts the same collection but does **not** compare money
with the next stake. Fewer than five cards selects the same card-short dialog 54,
sets scene state 3, and emits nothing. With five cards and balance 0 against a
new stake 400, the original sender at `1010D387` emits:

```text
GTM0G43001100@Continue=/Ans=1/PR=0/Conf=1
```

The scene moves to state 5, whose established receiver waits for ComList04,
not a normal Continue11 acceptance. See
[Computer menu evidence](NATIVE_OPPONENTS_NOTES.md). The current server preserves
this distinction: Conf1 returns the menu without authorizing/debiting a round;
the next selected ComGame must authorize its actual settings. Applying the
normal next-wager affordability test to Conf1 would prevent this original path.

## Leave and server policy boundary

The explicit-leave sender `1010D00B..1010D021` emits:

```text
GTM0G43001100@Continue=/Ans=2/PR=0/Conf=0
```

The existing generic stop reply is already supported elsewhere, but this probe
does not establish an additional funds-specific/card-specific negative response
or any historical server retry policy. A normal client with synchronized state
cannot produce the invalid normal yes described above. A manually injected or
stale request can still reach authoritative server checks; retaining their
transactional rejection and existing connection resynchronization does not
obstruct the proven normal-client paths. No new response or runtime policy is
introduced by this investigation.
