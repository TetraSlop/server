# Original ordinary-table eligibility

The native human Join button checks owned card count, the advertised table state,
wager affordability and an inclusive collection-CP range. Empty-table creation
uses a different branch, so applying every existing-table check to the first
owner would be incorrect.

`tests/fixtures/table_eligibility.json` preserves 31 original-instruction cases
using the current `table_metadata.table_info` encoder and original PTL decoder.
It extends the earlier 21-case external `table_eligibility_vectors.py` probe with
empty fixed/custom rooms, explicit CP-versus-rating evidence and the pending-owner
distinction. These are native client eligibility checks, not recovered historical
server-side rejection policy.

## Fields and existing-table gate

The original derived table record contains:

| Field | Offset | Meaning used here |
| --- | --- | --- |
| State | +18 | Selects empty, available or unavailable table branches. |
| Room context | +1C | Zero for fixed room rules, one for custom rules. |
| Wager | +20 | Copied into Join UI object+168 at 10045B0F..10045B12. |
| lu / ll | +30 / +34 | Inclusive upper/lower collection-CP bounds. |
| au / al | +38 / +3C | Decoder substitutes 300/100; not checked by this Join gate. |
| Occupancy | +54 | Existing-table gate requires fewer than 3 players. |

For an unregistered human, `1004785B..1004792F` requires:

- At least five owned cards, from `100B8B30(collection,0x406)`.
- Occupancy below 3 and state outside 2/3/4/5; state 6 is disabled separately.
- Money at `102B64A8` at least the advertised wager.
- `ll <= (WORD at102B63E0) <= lu`, at `100478D9..10047913`.

That WORD is **collection CP**, although the UI labels the range “Card Level.”
`101015C7..101015DB` calls the recovered collection-points function `100B9040`
and stores its result in player+0. It is not the derived level/tier byte at
player+AD, nor the rating DWORD at player+4. The fixture changes rating to 99999,
tier to 255 and the adjacent high WORD to ABCD while CP remains 15; bounds 10..20
still accept it. CP 9 and 21 fail, CP 10 and 20 pass. Money 49 fails a wager of 50;
money 50 and 51 pass.

The original gate's occupancy threshold is 3. A replacement table configured for
two players can additionally enforce its configured two-seat capacity; this
probe does not redefine that local capacity policy.

## Empty-table and existing-member distinctions

The empty branch is selected by **record state 0**, not by zero occupancy or owner
identity. `100477EF..10047856` still requires five cards, but it never reads
`ll/lu`. For fixed room context 0 it checks money against the advertised wager.
For custom context 1 it skips that money check. Original projection clears empty
custom game rules and empty table options; fixed game rules remain available from
the room preset. The first owner therefore chooses their own custom rules only
after entering.

A table can temporarily advertise state 0/count 1 before its owner applies
settings. Its rule predicate still takes the empty branch, but the separate
earlier guard `100475D3..1004768A` disables buttons for low flags equal to 2.
The fixture records this earlier guard separately. Its `pending_owner_still_state0`
case does **not** establish that another player can click Join during that phase.
Using existing player count alone to decide whether CP restrictions apply would
misidentify this state.

Already admitted owners/members use different table-button modes selected at
`10045B3D..10045B95`; `100477BC` bypasses the unregistered Join checks for those
modes. Repeated admission/Req must not make an existing member newly ineligible
merely because the owner changed bounds that exclude that member. A safe adapter
enforcement boundary is a new participant, with CP/wager checks corresponding to
the published state 1/settings-applied phase, plus the fixed-empty wager check.
Applying the checks to a direct new human Req is a replacement-server consistency
policy; the normal native flow already performed them before GameEN.

The Computer action is separate: its recovered entry gate requires five cards
but does not impose the human Join CP or wager checks before its own rule menu.
See [ROOM_COMPUTER_GATE.md](ROOM_COMPUTER_GATE.md) and
[COMPUTER_ROOM_RULES.md](COMPUTER_ROOM_RULES.md). Observer admission is also a
separate path and must not inherit participant inventory requirements.

## Passwords, rank bounds and errors

Do not add password authentication to GameEN/Req: those messages contain no
password proof. The original client compares entered text case-sensitively with
the publicly advertised metadata password at `10049742..100497EE`, then posts
its local admission action. See the password section of
[MATCH_SERVICE_NOTES.md](MATCH_SERVICE_NOTES.md). Likewise, these Join instructions
do not establish any `au/al` or rating eligibility check.

The probe establishes disabled-button conditions, not dedicated historical
negative response codes for CP, money or card-count failure. Returning an already
mapped nonfatal unavailable/start-failure response when a direct request fails
these checks is an explicit replacement-server compatibility policy. It must not
be described as a recovered distinct historical error for each condition.

## Reproduction

```text
python -B research/build_table_eligibility_fixture.py --research-dir <external-research-directory>
```

The directory contains the established `protocol_map` Unicorn harness and
reconstructed `TM_emulated_mapped.dll`, code-image SHA256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
The 31 cases pass; local output is `results/table-eligibility-original.log`.
The builder writes only its specified fixture. The complete original PTL
projection and Join predicate run against synthetic memory. Collection count is
substituted, actor statistics are seeded, and execution stops before widget or
rendering calls. No native application, physical input, listener, original asset
write or live save is involved.
