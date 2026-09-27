# Original client rule primitives

`native_rules.py` ports the card-state arithmetic of original TM battle100D31D0, growth100B3630 and CRT rand101E2E44. The pure module uses only the Python standard library and has no asset/file/network/UI access. MatchService now uses it for authoritative combat and card growth. Historical hosted RNG seeds, draw order and reward policy remain unknown.

## Public API

```python
from native_rules import CRTLCG, GrowthBaseline, calculate_battle, grow_card

rng = CRTLCG(1)                      # New server seed policy, not a recovered server seed.
saved_state = rng.state              # uint32; assign it to restore the exact stream.
result = calculate_battle(attacker, defender, rng,
    attacker_modifier=0, defender_modifier=0,
    attacker_property=0, defender_property=0)
a_tuple = result.attacker.wire(source_index)
d_tuple = result.defender.wire(target_index)
# Each tuple is (index, start, final, stat_selector, multiplier).

baseline = GrowthBaseline.from_catalog(catalog.cards[card.model], catalog.prices)
growth = grow_card(card, baseline, mode=0, random_value=shared_R[0])
updated_card = ExistingCardClass(*growth.card.full())
```

Both match-service and economy-service Card dataclasses are accepted by attribute. Growth returns this module's immutable `Card`, preserving model/arrows/deck and providing its eight-field numeric tuple via `full()`. `GrowthResult` also exposes `sale_value` and `changed_field`; the latter describes attack/type/defense mutation, not a points-only normalization. Caller supplies the correct model's baseline and retains responsibility for catalogue bounds, ownership and inventory eligibility.

`calculate_battle` accepts any card-like object with attack/kind/physical/magical integer fields. Signed modifiers must be in -128..127; full properties are bytes, with only their low nibble used by this calculator. Raw draws are injected through a callable; the supported draw domain is0..2147483647, including the original CRT's0..32767 range. Results expose `raw_draws` so tests/replays can preserve actual consumption. Bad static inputs fail before drawing; a failing draw source can already have advanced its own state.

## Battle fidelity and integration contract

- Types0/1 use attack against physical/magical defense; type2 uses the smaller defense; type3 uses the attacker's largest and defender's smallest of all three stats. Equal stats prefer magical, then physical, then attack for display selectors8/4/1. Signed modifiers are added after selection and effective values clamp to1.
- Attacker property low1 and defender low2 independently consume an extra raw draw for multiplier `rand()%4+2`. High nibbles do not alter this check. `start` is the unmultiplied base; the separate multiplier must remain in BattleData.
- Four raw draws supply the two modulo values per side, masked to16 bits and averaged with integer division. If both finals are0, exactly two extra draws replace them with their low bits. There is no further fallback or decisive reroll within the original calculator.
- `comparison` is1 for attacker win, -1 for defender win,0 for tie. `calculator_return` reproduces the original helper's1/2 return, which returns2 on ties too. **Do not interpret that return as the battle presentation's final winner.** `tied` is the safe explicit predicate.

For a tie, emit the tied BattleData and then another BattleData for the **same target and turn sequence**, without waiting for another BattleSelect and before changing owners or applying promotion. Native combat loops0B→0C→07→08 (`100CFD81..100CFD8D`). MatchService implements this through bounded scheduled continuations. It preserves ties and never forces a decisive winner; the watchdog aborts stalled processing through the ordinary disconnect/reload policy.

The module does not classify neighbors, flip cards, run special effects, choose a move, create shared R bytes or serialize packets. Battle raw draws and the eight shared R bytes are distinct inputs; their historical coupling is not known. An authoritative server must supply identical BattleData/R to all clients and maintain the recovered turn-ack boundary. Reusing local CRT arithmetic is a chosen replacement-server policy, not evidence of the original online random stream.

## Growth fidelity and integration contract

`mode&255==0` promotes types0/1→2 for `(R&255)<4`, type2→3 for `<1`, and leaves type3 unchanged. Any other mode byte chooses attack/physical/magical by `(R&255)%3`, incrementing only below that catalogue baseline. Existing over-cap values are preserved. Points and sale value are recomputed even without a stat change. Points retain the original low-byte wrap.

Growth omits the original visual indicator, positioned from rendered-card coordinates. Sale arithmetic preserves the original x87 operation order and rounds each intermediate with an explicit significand. `grow_card(..., x87_precision=64)` is the default used by the existing original-code harness;53 is also supported and tested.100B3630 inherits the process control-word precision, so this pass does not assume the native process always uses64 bits. Both use round-to-nearest/even arithmetic followed by truncation for the sale integer. Nonpositive baseline stats are rejected: all250 actual models have positive baselines, and malformed x87 divide-by-zero behavior is outside the supported domain.

Combat mode0 applies to the winning card when G.197==0: source G.179 after attacker win at100CFEC0..100CFEE0, or defender G.17A after defender win at100D0477..100D0495, using shared R0. Apply the mutation before a following battle in the same cascade. Result mode1 applies only at the recovered eligibility gate (player result class<3, valid card) and uses `R[(handSlot-3*serverSeat+2)&7]` at100C8F43..100C8FAD. The primitive does not choose those eligibility/indexing rules for its caller.

Integration retains stable original selected-slot identity through hand compaction and board reordering. TCList follows the original board-scan hand reconstruction; persistence updates those exact selected copies in their saved inventory positions. Result statistics and growth commit atomically before ResultData/TCList delivery. Duplicate Ready and rematch do not repeat a commit. Prize transfers and ranked wager/reward policy remain separate work.

## Verification

Run `python -B -m unittest test_native_rules -v` from `server`. **16 tests pass with11,772 direct comparisons against original instructions:**

| Component | Comparisons | Coverage |
|---|---:|---|
| Battle |4,244| Exhaustive0/7/255 three-stat ordering for both sides and all four types; all multiplier rolls; zero fallback/ties;1,200 seeded randomized valid cases including signed modifiers and full property bytes. |
| Growth |6,248| All256 R bytes × four types × both modes; all250 actual models at zero/half/baseline/over-cap values for each selected stat;600 randomized/narrowing cases;600 additional cases with x87 precision53. Card fields, derived points, sale value and changed field agree. |
| CRT rand |1,280| Five boundary seeds,256 consecutive outputs/state updates each. Only native thread-local storage lookup is substituted. |

The battle oracle executes100D31D0 with deterministic RNG substitution. The growth oracle executes100B3630 with the actual decoded catalogues; only rendering/runtime boundaries from the existing result harness are substituted. Tests also cover immutable inputs, explicit domain errors, state restoration, native selector ties and adaptation to both existing Card classes. These are original-code arithmetic tests, not a new native rendered-match validation.
