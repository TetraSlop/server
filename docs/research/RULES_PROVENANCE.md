# Rules and computer-opponent provenance

The original PC client contains substantial gameplay logic, including a local battle calculator and local AI. The present server integrates their exact arithmetic/decision functions for ordinary CPU and two-human matches. Seeds, CPU personality/deck, no-stakes rewards and disconnect policy are explicit local choices. Special-board, alternate-menu and other modes are tracked in EMULATION_COVERAGE.md; a mapped schema alone does not establish complete emulation.

Current implementation supersedes the initial reconnaissance status column and
next-step list below: native_rules.py and native_ai.py are integrated; ties are
preserved, shared-R promotion/result growth is authoritative and durable, and
result counts/streaks survive table changes. See MATCH_SERVICE_NOTES.md,
NATIVE_AI_NOTES.md, SERVER_RESULT_GAPS.md and SESSION_RECOVERY_PLAN.md. The
addresses, original behavior and offline-mode evidence below remain applicable.

## Initial reconnaissance checkpoint (implementation status superseded)

There is also real **network-independent simulation code**. That establishes more than the existence of packets named ComGame. It does not yet establish a supported, fully working offline mode accessible through the released menus. No desktop control, native process or network was used in this pass; original/repository files were read only.

All evidence addresses are original TM VAs, image base10000000. `P=102B63E0`, `G=DWORD[102B63C4]`. Existing detailed reports live in `../protocol_map`; new reproducible evidence is `rules_provenance_vectors.py/json`: **15 passing groups**, including64 original CRT random draws. Fixtures execute original instructions with explicitly seeded states and runtime boundaries.

| Area | Recovered from original client | Present practice server / remaining gap |
|---|---|---|
| Cards and ordinary board | Four combat types; eight arrow directions; sixteen-cell two-player and twenty-five-cell three-player boards. Actual card/catalogue fields and derived points are mapped. | Two players, ordinary empty4x4 board. Card selection validates against the authoritative economy. |
| Placement and captures | Original classifier100D3760, candidate scan100D38F0, deferred one-way captures100D2FF5, one-hop combo100D3590, win/loss/rescan100CFE4B..100D06AB. A source loss abandons deferred direct captures. | These ordinary capture/cascade rules are implemented. This is code-derived behavior, not invented Tetra Master rules. |
| Battle strength |100D31D0 chooses attack/defense by type, adds signed modifier, clamps to1, applies special multipliers, and calculates final values from four raw random draws. Online combat skips it when P.EA!=0 and waits for authoritative BattleData (100CEE51..100CF10F). | Uses recovered base-stat choices and a similar two-draw average, but Python uniform draws, no board modifier/special multipliers, and forced decisive outcomes. This is **not an exact port** of the complete local calculator or proof of the old server's algorithm. |
| Ties and random inputs | Equal final battle values cause another BattleData wait for the same target/sequence, without BattleSelect (100CFD81..100CFD8D). R[0..7] controls real shared growth/effects. | Current server rerolls ties up to16 times, then forces an attacker win; R is fixed64..71. Both are explicit practice policy. |
| Growth and promotion |100B3630 mode0 promotes type0/1→2 for R<4, type2→3 for R<1. Mode1 increments one of attack/physical/magical stats selected by `(R&255)%3`, capped by that model's catalogue baseline. Result eligibility/indexing is recovered at100C8F43..100C8FAD. | Battle promotion is suppressed by fixed R. Any result growth animation is reconciled back to original selected stats by TCList. Persistent growth is not implemented. |
| Special board cells | StartData F1..18 maps walls/effect cells/spinners; signed modifiers, all-direction arrows, ownership change, deterministic shuffle, CBC replacement and full spinner cascade/rotation are mapped and original-code tested. | Disabled/rejected by current settings. Client applies effects locally, but server must mirror their resulting board before selecting later outcomes. |
| Results and prizes | Local ranking/prize order, capped stat growth, one-card/perfect capture transfer, and local-only reward arithmetic are recovered. Online money/rating/history are received from ResultData; final card stats/owners from TCList. | Zero wager/reward, unchanged rating, restored cards, no persistent match rewards. Online historical reward/rating policy is not recovered merely because offline arithmetic exists. |
| Opponent choices | Local AI placement100D68D0, scoring filter100D69C0 and evaluator100D6AA0; target chooser100CEB2B..100CEB96. Placement uses personality bytes184/185/186, sometimes scores candidates, then chooses from the maximum-score set. | Current CPU chooses its first remaining card, first empty cell, and first battle target. It does **not** reproduce the original AI. Full heuristic/player-catalogue semantics and hand-choice policy remain to port. |
| Timing and authority | Native timer expiry submits ordinary choices. TurnData acknowledgement and destructive queue boundaries are recovered. | The all-human acknowledgement barrier is a new server orchestration derived from those gates. No historical server scheduling, retry or disconnect policy is claimed. |

The local calculator uses `final=floor(((r0&65535)%(base*multiplier+1)+(r1&65535)%(base*multiplier+1))/2)`. If both finals are0, it draws two additional `rand()&1` values once. It does not reroll every tie inside that helper. Native CRT function101E2E44 is also recoverable exactly: `state=(state*214013+2531011)&0xffffffff; rand=(state>>16)&32767`. The new vectors substitute only thread-local storage lookup and verify all64 outputs against this recurrence. Historical online seeds and draw ordering remain unknown; reproducing this client-local RNG would still be a consciously selected replacement-server policy.

## What the offline/computer branches actually prove

Constructor100B9A00 reads global102B624C and writes its nonzero predicate into both G.172 (receive gates) and P.EA (send/local-calculation gates), at100B9A89..100B9A9C. For constructor mode0/alternate0:

- **Global0:** branch100B9B46..100B9B56 chooses the local path;100B9BA7 sets G.171=1, enabling simulated-player choices.100B9C94..100B9CA7 locally generates one card for every catalogue model. It enters major2, whose100BCC28 branch skips ComGameInit initialization when G.172==0. Sends and normal gameplay receive waits are separately bypassed by the zero flags; the local battle and reward calculators become available.
- **Global nonzero:**100B9D1B..100B9D2B chooses network computer setup;100B9D88 sets G.171=0. Major2 calls100BAD30, which waits for43/02 ComGameInit. Local AI being present does not make this hosted computer route server-free.

The constructor-mode fixtures execute the original flag assignments, branch selection and major2 call gate for global0,1 and12345678. The computer-init callee is replaced with a returning stub only to count whether it was called; initialization/rendering are not played.

There is a concrete all-zero constructor caller at10191237..1019124F in event handler10190DD0, reached by its event01FF1003 branch (10191098..101910A3). That is a code-level entry lead, **not proof the normal released menu exposes it or that startup assets/profile/save handling are complete without a service**. Another caller1014A110 passes mode5 and must not be conflated with the mode0 local branch. Reaching the actual global0/mode0 path from the installed wrapper remains untested.

The hosted computer protocol is separate:43/02 ComGameInit supplies local stats/rules and optionally ComList;43/04 ComList supplies opponent entries;43/06 ComGame submits rules and selected computer IDs (receivers10102D21..10103039, sender10106F3A..101072E2, UI sends100EE95B/100EBBE9). Alternate Continue Conf1 waits for ComList04. Our server instead represents its CPU as seat1 in an ordinary VsGameInit match; it has not implemented these computer menus or the original NPC roster.

## Safest next implementation steps

1. Port100D31D0 as a pure deterministic battle function with an injected raw-random stream. Match original stat-selector tie ordering, signed modifiers, multipliers, exact modulo arithmetic and zero fallback against the existing `turn_pacing_vectors.battle` executor. Preserve equal outcomes and implement the already recovered repeated-BattleData tie path. This removes known approximations without needing an offline UI or an assumed historical server.
2. Port100B3630 growth using catalogue baselines and extend the existing51 original growth fixtures. Apply identical shared R to server state and all clients, emit the grown authoritative TCList, then commit through the economy. First explicitly choose whether practice retains no-stakes/no-transfer policy; growth/ownership persistence cannot be added only to animation packets.
3. Port original local AI with the same supplied random stream, beginning with100D68D0→100D69C0→100D6AA0 and its player parameters, then hand selection. Current seeded fixtures prove branches, not complete heuristic equivalence. Separately finish ComGameInit/ComList/06 orchestration if the original computer-selection menus are desired.
4. Enable special cells incrementally only after server parity with the existing original-code vectors: property preservation, shared-R ownership/shuffle, CBC generation, spinner wrapping/cascades, then final persistence. Board placement probabilities and historical server policies must remain explicitly selected policies unless separately evidenced.

Relevant detailed references: `TURN_PACING.md`, `COMBAT_SEQUENCE.md`, `SPECIAL_CARD_EFFECTS.md`, `MATCH_TIMEOUTS.md`, `RESULT_REWARD_SEQUENCE.md`, `GAMEPLAY.md`, and `ECONOMY_DETAILS.md`. No match-service implementation changed in this provenance pass.
