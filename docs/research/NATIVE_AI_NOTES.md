# Original local computer choices

`native_ai.py` ports the PC client's full ordinary move decision: choose a hand index, choose its field, and select a reciprocal battle target when needed. It performs no I/O and does not mutate cards, hands, or board state. The source is actual client-local simulation code, **not evidence of the old hosted server's AI**. No native process, desktop control, or game launch was used.

The default `Personality(100, 0, 100)` is explicit local server policy using the recovered parameter bytes. It always scores placements by distinct marked capture/combo opportunities. It is not attributed to a named original NPC. NPC roster data, deck composition, and selection of the five pregame cards are outside this move-policy port.

## Integration API

```python
from native_ai import Personality, choose_move, choose_target
from native_rules import CRTLCG

rng = CRTLCG(seed=1)
move = choose_move(hand, board, owner, rng, Personality())
if move is not None:
    hand_index, field = move.hand_index, move.field
target = choose_target(current_battle_candidates, rng)
```

`hand` is the current compacted sequence of at most five card-like objects. Each card exposes integer `model` (0..249) and `arrows` (0..255); attack, defenses, type, points, and deck association do not enter these decisions. `board` contains sixteen entries, each `None`, the explicit `BLOCKED` wall marker, or an object with `card` and current `owner` attributes. Existing match `BoardCard` objects work directly. Two-player ownership is 0/1. Inputs should remain immutable for the call. The returned `Move` and provided `Cell`/`Personality` types are frozen dataclasses.

`raw_rng()` must return an original CRT-width value 0..32767. `poll_hand`, `poll_placement`, and `poll_target` expose exact per-poll decision branches, accepting a positive signed DWORD `denominator` (default60). They consume one pacing draw; a remainder >=2 returns `None` without further draws. The immediate `choose_move` and `choose_target` adapters use denominator1, which always passes while retaining that pacing draw. **This changes visual scheduling deliberately:** it does not consume the unsuccessful frame polls that a rendered client would have consumed. Identical seeds therefore need not reproduce a whole original play session.

Empty hands, fully occupied/blocked boards, and empty target lists return `None` without drawing. The original picker assumes a nonzero count and would divide by zero if called outside that precondition; its original empty-cell scanner was separately executed and returns0. The server boundary handles that state safely. A sole battle candidate is returned without RNG: the original combat classifier bypasses target selection entirely in this case. With multiple targets, keep the supplied candidate order. Native100D38F0 scans targets in ascending field order; the match adapter's sorted candidates have that order.

The independent `choose_target`/`poll_target` accepts keyword `board_size=16`
(default) or25. MatchService passes its actual board size. This fixes automatic
sole-target battles on the last two rows of three-player boards without enabling
three-player placement scoring. The original target branch consumes candidate
bytes directly and performs the same rate/remainder operation for all seats.
An additional96 original polls cover all three owners, high targets, candidate
order and rate gates; an original5x5 classifier fixture takes sole target24 with
no RNG.32 complete randomized three-human matches cover targets16..24.

## Exact recovered decisions

| Decision | Original instructions | Ported behavior |
|---|---|---|
| Hand |100C42DE..100C4339 | Simulated-player gates precede the picker; rate test, then `rand()%remaining_hand_count`. Hand stats/personality are not examined. |
| Empty fields |100D3A60 | Scan ascending indices and include current owner8 only. Ordinary empty cells map to `None`; unavailable walls map to `BLOCKED`. |
| Placement |100D68D0..100D69BD | Rate test, score-chance test, optional preference-chance test, generate candidates, optionally retain maximum-scoring candidates, then choose by random remainder. |
| Maximum ties |100D69C0..100D6A99 | Compute one score per candidate, retain every equal maximum in original ascending order. |
| Scoring |100D6AA0..100D6E77 | Arrow/ownership marker union and literal byte-score formulas below. |
| Model priority |100B8680 | Read CardPrm model byte5 (price tier), then evaluator stores its low nibble in marker high bits. |
| Multiple targets |100CEB2B..100CEB96 | Simulated-player gates, rate test, then random remainder into the existing candidate list. |
| Zero/sole target |100CE481..100CE553 | Bypass selection: no AI RNG. Zero transitions to completion; one transitions directly to battle. |

The three player parameters at `P+184/+185/+186`, stride30 per player, are unsigned bytes:

- `score_chance`: compare `rand()%100 < byte184`. Values100..255 always pass.
- `preference`: byte185 selects a scoring mode. Values0 or5..255 use the ordinary affected-card count.
- `preference_chance`: after the score test passes, compare another `rand()%100 < byte186`. Failure substitutes preference0; it does **not** disable scoring.

Successful hand choice uses2 draws. Successful placement uses3 draws without scoring and4 with scoring. A default immediate move consequently uses6 draws. Multiple-target choice uses2; sole target uses0. Rate rejection uses1 for that poll. These counts and conditional order are checked against original execution.

For each empty field, the evaluator clears sixteen marker bytes. Follow the selected hand card's arrows to opposing cards. An opposing card with no reciprocal arrow gets marker bit1 (direct capture). A reciprocal opponent gets bit2 (battle); additionally, follow that opponent's arrows one hop and mark its same-owner neighbors with bit4 (potential combo), regardless of those neighbors' reverse arrows. There is no recursive chain beyond this one hop. Marks combine with OR, so overlaps count once in the base score. This predicts opportunities, not guaranteed captures or expected battle wins.

Every marked card also receives `(CardPrm.price_tier << 4) & 255`. This is **not card points or an unbounded rarity number**: catalogue tiers16..24 wrap to nibble0..8, as the original byte instructions require. The port's250-entry immutable table matches the independently decoded `protocol_map/economy_catalog.json`; its CardPrm decoded SHA256 is `08a7007f66c1d87caa412f2d023b9a9d8d19a2e5a80b8f3e3265aa52b23f2a5b`.

Let `A` count any low marker bit, `B` count bit2, `C` count bit4, and `T` be the largest stored tier nibble. Exact two-player scores are:

| Preference | Score |
|---|---|
|0 or5..255|A|
|1|16+T if T>1, otherwise A|
|2|16+B if B>0, otherwise A|
|3|For **every** board cell without bit2, add2 plus1 if bit1 is set; even unmarked empty/wall cells contribute2.|
|4|16+C if C>0, otherwise A|

These literal formulas preserve oddities in the original routine. In particular, mode3 gives32 on an entirely unmarked board. No attack/defense comparison or probability-of-winning calculation is silently added.

## Validation and limits

`python -B -m unittest test_native_ai -q` passes 14 tests, including **1,865 original-code comparisons**: 1,078 scoring results across 20 boards and seven modes; 140 full moves; 448 hand/target rate polls; 100 placement threshold/rate polls; one sole-target classifier; one fully blocked candidate scan;96 three-player target polls and one5x5 sole-target classifier. The complete selected outputs and consumed random streams agree. Cases cover empty/blocked cells, both owners, zero/all/diagonal arrows, direct and reciprocal captures, one-hop combos, overlapping marks, wrapped price tiers, tie order, and boundary random values.

The oracle executes original x86 with Unicorn using the existing `TimerVM` fixture. Original adjacency tables, scoring, maximum filtering, and CardPrm lookup remain executable instructions. Only raw CRT draws and the fixture's existing presentation/runtime boundaries are intercepted; the ordinary AI paths invoke no substituted decision logic. Original mapped-image SHA256: `422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.

Placement scoring covers ordinary4x4 two-player play, including unavailable wall markers; it rejects special-card IDs and larger boards. Target selection separately supports explicit16/25-cell bounds. All-direction tile properties, spinner handling, special ownership/effects and three-player placement scoring remain outside this module. Original NPC personalities/roster composition are supplied by native_opponents; special placement is supplied by native_special_rules. Battle calculation, growth, final ownership and persistence belong to their separate recovered rules and service layers.
