# Result bookkeeping and remaining policy boundaries

Current implementation adds opt-in human/CPU ordinary card prizes and monetary
stakes to the original practice path described below. Exact winner choices,
atomic ownership settlement, overflow edits, delayed deck editors, double-up,
payout D1/statistics and interruption recovery are integrated. See
MATCH_PRIZES_NOTES.md, MATCH_ECONOMY_NOTES.md and RESULT_REWARDS_NOTES.md for the
current APIs. The historical online rating and award policies remain distinct
from these explicit local choices. This document retains the original field
evidence and identifies which older practice choices remain the defaults.

The ordinary no-stakes practice result now carries account statistics consistently into the next profile load. `result_stats.py` supplies one immutable `PracticeStats` value; `MatchService` uses that value for ResultData and its final-card snapshot; `EconomyService.commit_match_growth(rounds, stats_by_key=...)` validates and commits the same statistics with grown cards. The adapter seeds each table from the authenticated account profile and persists before sending result acceptance. This avoids table-local counters replacing a player's prior history while the saved profile retains different values.

This audit and implementation used original instructions and isolated local tests, without a native game/UI launch. “Original” below means this PC client's behavior. Historical hosted reward/ranking policy remains distinct from a compatible replacement-server policy.

## Mapped statistics and authority

Ordinary ResultData receiver `10104141..101044F9` accepts the initialized service sender. Only the local ordinary player branch updates the personal totals; peer/NPC branches chiefly use the received rating. Client result setup `100CBD80` calculates current match ranking and presentation flags from board scores, independently of the neutral D0 field.

| Result field | In-memory destination | TM0DataFile offset | Server responsibility |
|---|---|---|---|
|D0|No established read|None established|Remain neutral; do not label this win/loss or rank.|
|D1|P+CC signed pending reward|Final money at34|Online server supplies the reward; the animation adds positive values only. Zero is coherent for no-stakes practice.|
|D2|P+4 and player+180 rating|30 DWORD|Preserve or deliberately calculate an authoritative rating.|
|D3|P+88 signed streak|78 WORD|Current streak must match the saved value; a local win can update best streak at file7A.|
|D4|P+86 sequence|44 WORD|Preserve account continuity across tables/reconnects. Native inventory UI specifically tests equality to1 at100CB856/100CB9B3.|
|D5|P+84 cumulative divisor|40 WORD|Must be nonzero in a completed ordinary result:100CC8F1..100CC906 divides D6 by it. This is not a wins counter.|
|D6|P+80 cumulative reward sum|50 DWORD|Must agree with saved total and derived average at4C.|
|Derived maximum|P+78|48 DWORD|Online nondraw setup can raise it from positive D1 at100CC8DD..100CC8EC.|
|Derived best streak|P+8A|7A WORD|Online winning setup compares D3 as signed16 at100CC69E..100CC6B3.|
|T0/T1|G+1D8/+1DA temporary deltas|RT total at128|Do not confuse these with card points or money. Their animation is gated by G+18A==2; exact historical bonus names remain unknown.|
|T2|P+B8|130 WORD|Final VS. Player Games count; advance once for a completed human match, with WORD wrap. CPU and borrowed event rounds preserve it.|
|RN|P+128 text|7D,32 bytes|Keep bounded and preserve existing text unless a policy actually changes it.|
|Prize points|P+B4|134 DWORD|Separate currency, initialized/spent through Prize Center; ordinary ResultData is not its purchase flow.|

The profile importer addresses are `101011FD..101012D2` and `1010121D..1010125F`. It does not establish independent total wins, losses, draws, or perfect-win counters in these mapped header fields. Those can be retained in a new internal match ledger for future features, but writing them into unrelated or opaque profile slots would invent a wire schema.

Four distinct quantities must stay separate: current board card score, collection CP, rating, and VS. Rating and prize points. Collection CP is derived from unique-model maximum points, per-card type bonuses and distinct arrow masks (`100B9040`, `ECONOMY_DETAILS.md`). The economy already derives CP when serializing the grown inventory. Profile loader/refresh recomputes card level (`1010146E..101014AD`, `101015CA..1010161A`); the current serialized level cache0 is not an instruction to reset the calculated level permanently.

## Implemented no-stakes policy

`PracticeStats.from_profile(economy.profile_bytes(key))` loads the existing mapped values. `next_result(outcome)` accepts -1 loss,0 draw,+1 win. The following choices are explicit local practice policy:

- Practice counts as one completed two-player result: D5 increases by1. It saturates at65535 to prevent the native unguarded division from receiving0. The original offline count increments by n-1 and can wrap; this safety boundary is deliberate.
- D4 increments modulo65536, matching the original WORD sequence. Its equality-to1 UI branch can recur after a full wrap.
- Signed streak uses the original transitions at100CC64C..100CC69C: draw resets0; win after negative becomes1, otherwise increments; loss after positive becomes-1, otherwise decrements. WORD overflow is preserved exactly, including sign wrap at32767/-32768. A win raises best streak using the original signed comparison.
- D1 remains0. Money, rating, cumulative reward sum, maximum reward, RN, VS. Rating and prize points are preserved. Human matches advance VS. Player Games; CPU matches preserve it. The average is recomputed as existing sum/new count, so zero-reward practice can lower a previously nonzero reward average. No ranked-money formula is implied.
- T is `0|0|final_vs_player_games` (legacy API name `event_count`); RN contains the existing bounded bytes. A malformed legacy RN with all32 bytes nonzero is normalized to31 bytes so the native unbounded copy has room for its NUL terminator.

`result_section(seat)` builds the exact D/T/RN section and rejects a zero D5. `apply_to_profile(profile)` changes mapped fields only, preserving options, unrelated header bytes, inventory, deck names and money. The economy accepts only a snapshot equal to one legal transition from the current profile. All participating accounts and card changes are validated before either account is mutated. Existing trusted card-only callers may omit `stats_by_key`; the production match adapter supplies it. Old reservations cannot replay a committed round; refreshed reservations support rematches. Options edited during a reserved match remain intact.

The original offline rating calculation is separate: `100CC7E8..100CC840` writes a class into a32-entry local ring and averages `(class+2)*50`. Online mode bypasses this and receives D2. The opt-in `--ratings local-history` policy now implements and durably stores that ring; see RATING_POLICY_NOTES.md. It is an explicit local policy, not evidence of the historical online formula.

## Prize and result flow already coherent for practice

Original match classes are1 win,2 draw,3 loss for two players (`100CC566..100CC6DB`); perfect state derives from total captured cards. These presentation flags are calculated locally from authoritative board ownership. They are not D0 and do not require fabricated server flag fields.

The final TCList carries each card's original player as its final current owner in no-transfer practice, while retaining grown stats. That intentionally produces no eligible loser-card prize candidates: candidate construction at100C9136..100C9203 selects loser cards currently owned by the winner. The implemented GetSelect exchange is enabled by the separate card-prize option. Native single-card/perfect automatic awards also have no candidate to transfer in default practice. No-stakes means no stake debit/refund or reward delta; this remains compatible with the recovered positive-money animation and continuation cost paths when the configured stake is0.

Ready is a client presentation boundary, not proof of a required Go=1 response. Existing result handling emits TCList once on the first Ready and avoids duplicating the final mutation on later Ready. Continue Conf0 rematch/leave uses the mapped header/Ans completion values. These lifecycle behaviors are covered in the gameplay agent's match tests; the source sequence remains `RESULT_REWARD_SEQUENCE.md`.

## Concrete remaining work

1. **Awarded cards are implemented:** actual capture owners, original compacted choices, automatic and CPU choices,43/10 Erase,43/22 Decks, atomic losses and durable pending awards are integrated. Mode2 remains a completion marker. The remaining check is native scene parity under user control.
2. **Money and optional ratings are implemented; historical policy remains unknown:** D1/D6 agree with actual capped credits and the saved profile. Human wagers use a zero-sum pot; CPU rewards reuse offline arithmetic. Stakes debit before successful initialization/rematch and interrupted rounds refund once. The original32-entry offline rating ring is a separate optional policy.
3. **RT, events and Prize Center:** EventResult remains separate from ordinary D/R/T/RN mutations. Prize Center's purchase/collection contract and durable explicit awards are integrated. Event rooms, F-card grants, two/three-player E matchmaking and event observers are integrated. Automatic mission eligibility, RT awards and historical schedules remain policy decisions.
4. **Ranking/profile presentation is implemented:** all71 PO fields, privacy updates, all5 ranking categories and owner-bound immutable query files are available. Historical categories require actual historical data; unknown fields are not repurposed as invented counters.
5. **Departure policy:** ordinary completed results are committed once. Opt-in practice departures implement substitute, invalid and loss branches with retained account provenance and original partial result/rank behavior. Economic/rated departures still abort/refund because partial ownership and historical penalty policy are unproved. See NATIVE_DEPARTURE_NOTES.md and DEPARTURE_ADAPTER_NOTES.md.

No user choice is required to keep practice coherent or to use the documented opt-in local money/card policies. Historical ranked rewards and award/reset schedules remain review items; none is attributed to recovered server behavior without evidence.

## Validation

`test_result_stats.py` has11 tests:42 original offline bookkeeping comparisons across signed-streak/sequence boundaries;3 original online ResultData receive plus result-setup comparisons; mapped profile roundtrips; coherent zero-reward totals; atomic two-account rejection; old-token replay rejection; persistence/restore and a second table; option preservation; and actual TCP profile reads after restoring saved results for both distinct accounts. `python -B -m unittest test_result_stats test_match_economy -q` passes17 tests. Original fixtures replace presentation/runtime boundaries only; statistic arithmetic and wire receive instructions execute unchanged. The count-saturation guard is explicitly tested as server policy, not falsely compared to the original divide-by-zero case.

Related sources: `RESULT_REWARD_SEQUENCE.md/json`, `result_reward_vectors.py/json`, `SHOP_INVENTORY.md`, `PROFILE_CONTRACT.md`, `PROFILE_FIELD_SEMANTICS.md`, `ECONOMY_DETAILS.md`, `PRIZE_CENTER.md`, and `FILE_COVERAGE.md` under `../protocol_map`.
