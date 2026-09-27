# Local practice match service

`match_service.py` is a synchronous table service. It does no networking or profile I/O; pass the economy's decoded `Catalog` to share its rule baselines (otherwise the constructor loads that local catalogue). The adapter owns the published table identity, IRC framing, registration and lifecycle. All replies for a match must have the same source service identity used for VsGameInit.

```python
match = MatchService(mode='computer', seed=1, catalog=economy.catalog,
    selected_card_validator=lambda peer, cards: economy.validate_selection(account_key(peer), cards))
match.register(session, character_id=1, name=b'LocalPlayer', nickname_id=advertised_nn,
               balance=10000, points=1,
               result_stats=PracticeStats.from_profile(authenticated_profile))
deliveries = match.handle(session, payload)
if deliveries is not None:
    for delivery in deliveries:
        # Send delivery.payload to delivery.peer from the table-service nick.
        ...
```

`handle` returns `None` for unhandled messages, `[]` for a handled request that has no reply yet, and `Delivery(peer,payload)` objects for responses/broadcasts. `MatchError` rejects invalid input; adapters should log it without inventing an acknowledgement. Public `playack(peer)`, `member_list(peer, include_id=False)`, `begin()` and `snapshot()` support explicit integration. `reset()` reopens a finished table, retaining registered human peers; call it only after old clients have left. A new service instance is preferable when membership changes.

## Original rule and persistence integration

The default path uses `native_rules.calculate_battle`, `grow_card`, and `CRTLCG`, plus `native_ai.choose_move`/`choose_target`. These are active defaults, not optional reference calculations. The original arithmetic and AI decision routines have separate original-x86 comparison suites. The new server deliberately chooses its seed, consumption schedule and default AI personality; their equality to the historical hosted service is unknown.

`draw=` may inject a callable raw random stream for deterministic tests/replays. The default is `CRTLCG(seed)`. The current schedule consumes eight low-byte draws for each TurnData/R; original AI gate/choice draws when a CPU decision is needed; the original calculator's conditional raw draws, then eight low-byte draws for each BattleData/R; and eight for ResultData/R. Shared R and calculation draws remain separate inputs. Explicit versus boards apply their original property/modifier inputs to the same calculator. `ai_personality=` defaults to `native_ai.Personality(100,0,100)`: original uniform hand choice and placement scoring favoring affected cards. Immediate CPU scheduling uses original rate denominator1, retaining its gate draws. This replaces the earlier first-card/first-empty-cell placeholder. Target candidates retain the original ascending board order (`100D392C..100D3979`); a sole target consumes no choice draws.

Tied calculator values are transmitted unchanged. A tie does not change owners, grow cards, advance the turn or request another BattleSelect. It sets `pending_work`; the adapter must yield to its event loop and call `advance(max_battles=1)`. That returns at most one BattleData per human, possibly followed by the next TurnData or ResultData. A decisive win may similarly schedule the next automatic cascade battle. `max_battles` explicitly permits1..16 for bounded testing; production should use1. There is no forced winner after a retry count, so an adversarial injected constant RNG can remain tied indefinitely without an unbounded synchronous call or reply burst. Guard scheduled work against table replacement/disconnection and call `cancel_pending()` when discarding a table. Wire Quit/Break, reset, rematch and completed result clear pending work themselves. No timer or guessed presentation-complete acknowledgement is introduced.

After each decisive battle, exactly the winning card receives mode0 growth with the transmitted shared R0, matching source/defender callers `100CFEC0..100CFEE0` / `100D0477..100D0495`. That promotion affects later battles in the same cascade. `BoardCard.original_owner` plus `original_slot` identifies the actual selected copy; `Player.hand_slots` compacts alongside the playable hand, while `selected` remains immutable and `updated` retains original slot order. Identical card copies therefore cannot accidentally share growth.

At result, the original reconstructs each hand by scanning ascending board indices (`100C87E2..100C88D9`). Mode1 growth for winner/draw cards uses the **reconstructed hand slot**, `R[(handSlot-3*serverSeat+2)&7]`, with the original result-class<3 gate (`100C8F43..100C8FAD`). Losers retain battle promotions but receive no stat increment. All final derived points are normalized to the catalogue, as the original final-list parser does. TCList uses reconstructed hand order and original practice ownership; this also preserves the original parser's untouched deck byte. It must not be serialized in selection order when placement order differs.

The adapter's persistence interface is:

```python
snapshot = match.final_cards(peer)  # None until an ordinary result completes.
# Immutable FinalCards:
snapshot.round_id   # Monotonic service-local completed-round number.
snapshot.original  # Tuple of5 full Cards in ORIGINAL selection-slot order.
snapshot.updated   # Tuple of5 grown Cards, in those SAME stable slots.
snapshot.stats     # Immutable PracticeStats serialized into this ResultData.
snapshot.extras    # Tuple of current-owner CBC rewards, separate from original5.
```

The snapshot is ready before the corresponding ResultData leaves `handle`/`advance`. Before delivering result replies, root should atomically commit original→updated **and stats** through its reserved economy account, deduplicating by **match instance/key + round_id + peer**. The service does not commit inventory or implement a durable transaction itself. Repeat `final_cards`/Ready/Continue calls do not recalculate growth or statistics. Snapshot objects remain immutable and accessible through Continue until all players select the next round; callers may retain them afterward. `reset()` clears snapshots. The root adapter refreshes its reservation after a successful commit for rematch, and releases reservations on terminal leave/disconnect. No cards or currency are transferred by this practice policy.

Register with `result_stats=PracticeStats.from_profile(authenticated_profile)` to retain that account's history. The default only supplies an empty history at the given rating for isolated fixtures. Each completed round computes win/draw/loss from final current ownership, invokes `next_result` once, and uses `result_section(seat)` for D/T/RN. Zero current reward preserves rating, reward sum/maximum, money, RN and RT/event/prize fields. The explicit practice policy advances count/history and the recovered signed streak transitions; it does not claim historical online ranked rewards. Stats persist across rematches in the Player record and are also exposed in the immutable result snapshot. `round_id` remains a service-local commit identifier, separate from either on-wire history field.

Authentication is the supplied opaque `peer`, not any packet field. The first bounded sixteen-digit NN from lobby Req/GameEN is adopted as member/display identity only and then frozen in the pure engine; network adapters additionally require it to match the authenticated account. Admission HID is independent POL handle metadata, never a character selector. The captured native client sends HID0 for character1. Two human peers require distinct registered profile character IDs, even when both use handle0. Names are limited to15 printable ASCII bytes in this implementation. See ../protocol_map/ADMISSION_IDENTITY.md.

## Phase and replay boundaries

Admission, roster/start controls and table-UI cancellation are accepted only in their lobby phase. Late Req/GameEN/GameENC/GameM/GameOK/GameReady/GameQT/GameNG cannot mutate an active round or reopen a finished one. Start offers are owner-only and single-use; repeated Req after that peer's GameOK or repeated admission after Req is ignored. GameQT targeting another member is rejected because this practice service does not implement the separate kick/host-migration flow. GameExit completes once in lobby/finished; active gameplay aborts use the mapped game Quit/Break route first.

Ready checks the advanced sequence (10 for a full two-player round,15 for three players; actual advanced boundary for early departure results) and `Ans=authenticated seat` before first TCList delivery. Continue verifies the authenticated header seat and ordinary Conf0 before changing votes. Quit requires `No=authenticated seat`; its header is a **major client scene**, not the turn sequence. The supported major/phase gates permit initial05/selection08, turn0A (plus closing-selection08/09 before the first ack), and result0A..0D accounting for presentation lag. A late turn-major0A Quit therefore does not abort a new rematch's selection08. Break requires header3 and the authenticated profile character ID. Terminal duplicates cause no repeated growth or result commit, and abort/leave clears scheduled combat.

This wire format has no recovered match-round nonce. Byte-identical old and new round messages, such as sequence0 ack or Break3 with the same character, cannot always be distinguished by this service. Ordered authenticated transport, per-match adapter locks and membership-generation guards remain necessary. In particular a finished table must not be reset merely because an old participant sends another admission-shaped packet; the root adapter must wait for actual table leave/PART or a fresh membership generation. Rejecting provisional admission must also roll back its new account reservation and membership. These are server boundaries, not invented wire acknowledgements.

## Supported route

The root room service publishes the table in PTL/TD first. Table41/01 admission gives the owner EN1 and later human joiners EN2;41/12 computer admission gives EN1. The original EN1 branch opens owner configuration, whereas EN2 takes the existing-table route and its local wager subtraction. GameM gets the member list. GameReady offers the other registered human a start; Req gets PLAYACK plus rules/member data. Once every registered human sends GameOK after Req, the service emits41/09 and43/01 VsGameInit. It also supports the direct rule query and CheckJoinTable answer.

For an empty-table claim, the native client applies table settings with primary14 before Req. `handle14` validates and atomically stores current `game_rules`/`table_rules`, sets `settings_applied`, and increments `settings_revision`. It returns no wire acknowledgement: the original Apply at10053491 calls sender10087410 then immediately updates/posts its local editor event03000C27 and closes (`10053496..100534C7`). No response parser is invoked there. GameReady/Req are subsequent native Start-controller actions, not a fabricated settings handshake.

After that revision changes, the adapter should call `admission_notice(peer)` to materialize the CPU, publish coherent TD with `owner_id` and `len(players)`, then deliver the returned41/04 GameML and primary1A GameOwner notices from the table service. Both notices are mapped messages; they are roster/owner publication, not a settings ack. Receipt of14 proves the earlier GameEA cleanup has completed, so the roster will not be discarded by it. `owner_id` follows the first admitted human's advertised NN. `ready_to_start` means lobby phase, accepted settings and two configured seats; it does not claim the native UI has enabled its button.

The native Start gate also checks that **every roster member, including the CPU, exists in the room's derived player list with decoded status3/substatus1** (`1005C0D0`). The room adapter must publish the corresponding synthetic CPU PD/PTL identity; its `MatchService.players[1].nickname_id` currently defaults to200hex. A GameML count2 alone is insufficient. TD/PD encoding remains owned by the room service.

Rules13, PLAYACK and VsGameInit all reflect the accepted current settings. In particular the observed native14 with gs0/tl0 is preserved; no fixed60-second rule overwrites it. The seven init values are bm,st,cb,ca,tl,du,gs in that order. Current rank bounds are au300/al100: the original directory decoder supplies these fixed values and has no metadata fields to encode alternatives. The defaults agree, and active14 rejects other bounds atomically. Primary24 saves only the flagged Game/Tbl preference dictionaries in `saved_preferences[peer]`, without changing current table rules or revision; saved rank bounds may differ. The original save callers use the separate saved-rule globals (`100512ED`, `10053674`), then close/update locally. Preferences are in-memory here and have no invented Save acknowledgement or persistence claim.

By default, `computer` mode represents seat1 as Local Computer in the ordinary **versus table protocol**. The explicit `computer_menu=True` option instead implements the original GameENC/Req/ComGameInit/ComList/ComGame06 route and selects one of the30 original PlPrm NPCs, including its exact local-reference pool/hand generator and AI personality. See `NATIVE_OPPONENTS_NOTES.md` for the adapter API, original-code checks, menu timing, and limits. `versus` mode requires two registered humans and includes both in the start and turn-ack barriers.

CardSelect validates all five full records, optionally against the authoritative reserved economy multiset. It emits selection status and, only once complete, StartData followed by exactly one TurnData. Early moves are buffered until all humans acknowledge that turn. The server never periodically resends TurnData. Duplicate/stale acknowledgements do not replay a move. The computer chooses through the original local AI after the human ack. Each participant gets PutCard including the full card record.

Ordinary reciprocal-arrow targets are resolved with BattleData. One target needs no BattleSelect; multiple targets wait for the active human's selection. Attacker wins perform the one-hop combo and continue through remaining candidates; defender wins stop and abandon deferred one-way captures. One-way captures complete without BattleData. Board indexing uses nonwrapping4x4 neighbors with arrow bits N,NE,E,SE,S,SW,W,NW.

`PutData H` is the **current compacted hand index**, not a permanent original slot. Outgoing producer100C5E18..100C5E53 stages the card at `(active*5+G.17A)*9C` and copies that index toG.1B2; builder10107A84 serializes it. After placement decrements count, the active client copies each later record left through100B2A80 (`100C710F..100C7152`) and clears the new trailing slot (`100C7185`). Original-code tests play H2 from IDs10..14, observe `[10,11,13,14,FFFF]`, and show the next original H2 producer staging model13. Passive observers skip the local hand compaction and receive each full card through PutCard; they do not define the active sender's H numbering. The service's `pop(H)` and decreasing valid range therefore match the controlling client's hand.

StartData F0 with B0/E0 is a normal empty board. The original pre-start reset100BD8FC..100BD94E sets every board model toFFFF, all owners to8 and property to0. The generated StartData leaves B+E at0, and setup gate100C2AF8..100C2B0C jumps past all special constructors to100C2E5E. A fixture executes that reset, the full original StartData receiver and this gate; all16 cells remain empty with no special property.

At ten placements, ResultData has a nonzero D5 count from the account's seeded statistics. D4 history follows the original WORD increment/wrap; D5 saturates at65535, while the separate service round ID continues increasing. D5 narrows to a WORD in the original receiver and is a divisor at100CC8F1..100CC906, so allowing65536 to wrap to0 would be unsafe. The first result Ready from each human releases that human's TCList. Original ownership is restored in this final list, so there are no prize-transfer requests. Leave Continue gets header2/Ans3, not an echo of Ans2 that would leave the client waiting. Accept Continue waits for all humans, sends header1/Ans1 and returns to selection; it sends no early StartData that the rematch queue clear could discard.

The result Ready at100C8072 and prize Ready at100CB310 are distinct presentation barriers, without a mapped retransmission indicator. Each human receives only one TCList per round; later Ready messages cause no extra response. Continue replies likewise are single-use after the service has left result phase. An original-code fixture executes the parent rematch cleanup100BF01E..100BF0AA and proves it drops subtypes02/04/08/09/0A/0C/0D/0E/1A/0F but **preserves11 Continue**. Replaying an already completed Continue could therefore contaminate a later result. This service relies on ordered session delivery and does not claim dropped-response or reconnect recovery; the protocol has no recovered round identifier in these result requests to distinguish delayed copies from another round.

Ordinary rematch clears the board, turn/sequence, ack barrier, pending move, combat targets and result-response tracking. Humans must choose five cards again; the CPU's configured five-card hand is restored. The table owner, accepted settings, profile inputs, and Req/GameOK membership acceptance remain intact because the original rematch returns directly to selection, without a new table-admission handshake. Original selection-constructor blocks100D71CD..100D71DF and100D7228..100D7266 reset local/remote selection status and counts; the next generated CardSelect Ans1|1/Ok1 restores both counts to5 in the original receiver. Full two-round CPU and two-human tests exercise this fresh selection/ack cycle. Conf1 is rejected before changing Continue votes: that original choice sends Ans1/Conf1 at1010D375 and waits for the legacy ComList04 menu at1010D5D9, which this ordinary versus-table practice route does not implement.

## Explicit configured boards

`MatchService(mode='versus', start_fields=fields)` accepts exactly16 original StartData F values. Default `None` is16 zeroes. The constructor rejects F18/CBC unless its explicit reward-integration gate is enabled, and rejects more than six blocking/effect targets, guaranteeing ten unconditional placement cells. The adapter may opt into a configured two-human or computer board; no random historical board distribution is claimed. `legal_fields()` returns ascending placeable cells, including bare low-property tiles whose current owner is8. It describes geometry; `handle` separately enforces phase, authenticated actor, hand index, sequence and acknowledgements.

The pure module `native_special_rules.py` supports16/25-cell original geometry and F0..18 construction (`100C1920..100C1BA3`). The hosted match remains16-cell/two-player and exposes F0..17 by default; F18 requires `allow_cbc=True` after adapter reward integration:

| F | State and behavior |
| --- | --- |
| 0 | Normal empty cell. |
| 1,2 | Unavailable blocks, ID8001/8002. |
| 3 | Placeable property1: attacker multiplier; consumed only when the complete source property equals01 at turn end. |
| 4 | Placeable property2: defender multiplier; retained on the card. |
| 5 | Placeable property3: all eight effective arrows for targeting and one-hop combos; retained. |
| 6,7 | Subtract/add10 to every current active player's signed-byte modifier, with native wraparound; consume target. |
| 8 | R1 selects the beneficiary, then all occupied player-owned adjacent cells flip without arrow checks; consume target. Losing the attacking card abandons deferred captures. |
| 9..16 | Spinner ID8009, direction F-9. Its ray points opposite its arrow, wraps, passes empty/block cells and can trigger other spinners. Every spinner rotates once per completed turn. |
| 17 | Shuffle ordinary cards in ascending original-index order into ascending base-owner8 candidates using R[oldIndex&7] modulo remaining count; consume target and end combat. |
| 18 | CBC replacement; **gated by allow_cbc=False**. Enabling requires the adapter reward/overflow/Erase transaction described below. |

StartData B counts nonzero F outside3..5; E counts3..5. Settings/init flags describe this explicit configuration: st for F3..5, cb for F6/7/8/17/18 and ca for F9..16. Current primary14 must match those flags; it does not invent a layout when a flag is enabled. Saved preferences remain separate. This explicit layout/flag policy is a replacement-server choice; F construction and effect arithmetic are recovered client behavior.

All owner4 effect targets pass through BattleData. The ordinary calculator supplies A/D values, with selectors zeroed as the original local controller does (`100CEE95..100CEECC`). Special effects bypass ordinary comparison/tie/promotion. Spinner range uses displayed A start, including A multiplier, rather than A final (`100D27B1..100D27F2`). Shared R controls ownership and shuffle. Effects do not request an extra acknowledgement; further reciprocal targets follow the existing bounded pending-work contract. Persistent markers prevent reselecting an activated spinner within a turn.

Temporary properties/modifiers stay in `BoardCard`, never inventory `Card` or final replacement snapshots. Shuffle preserves original owner/selection slot, deck, property and modifier. Current ownership determines scores; no-transfer practice results restore original card ownership. Sixteen complete configured rounds verify all ten selected identities survive and at most permitted permanent growth reaches each final card. Rematch reconstructs the same F board after fresh selection.

The special suite performs **2,528 original-x86 comparisons**: every F constructor, classifier property/marker/owner domains, every signed modifier, all R1 thresholds, random shuffle/adjacent ownership/combo cases, CBC compact copy, low1 cleanup, spinner directions/range boundaries and96 random chained-spinner boards. Nine hosted integration groups cover low-property multipliers, temporary-effect isolation, settings, full rounds and rematch. Generated BattleData for every F6..18 is consumed by the original parser and full combat dispatcher, comparing resulting boards. Presentation jitter/audio/drawing are substituted. Ownership applies during local elapsed16..31 (`100D15AE..100D1797`), so its fixture advances individual ticks there; coarse animation skipping would omit the mutation. This proves behavior under stated fixture timing, not a live rendered special-board match.

`native_special_rules.special_placement_scores(card, cells, active, preference=0)` is a separately verified original4x4 AI evaluator, with **1,050 further original-x86 comparisons** across40 mixed special boards and all seven tested preference values. It includes every current-owner8 cell, substitutes all arrows when placing on property3, respects property3 on opponent cards and potential combos, and predicts spinner rays for exactly two wrapping steps. That heuristic uses player count rather than battle strength, ignores spinner chains and other chance effects, and replaces an existing marker with direct-capture1 for a spinner prediction (`100D6C25..100D6CD6`). Those peculiarities match the original. The scorer itself performs no RNG or mutation. `poll_special_placement` now preserves the original rate/chance/preference/maximum-tie draw order, and `choose_special_move` combines the original hand picker with that evaluator using explicit immediate denominator1 scheduling. A further180 complete moves and180 rate/chance polls agree with original choices and every consumed draw. MatchService uses this adapter whenever a computer board has nonzero F; default empty boards keep the existing ordinary adapter. Twelve complete configured CPU rounds plus rematch and an original full-decision comparison validate the enabled path. The special suite is now16 tests/**3,938 original comparisons**:2,528 effects,1,050 scores,180 complete moves and180 polls.

## Explicit new policy and limits

- Default empty4x4 board; optional explicit F0..17 two-player board as above. Wagers/double-up are disabled by default and require the economic hooks below; active passwords use the validated eight-byte metadata field below. Settings inconsistent with the configured board are rejected atomically. Time-limit and quit-rule fields retain valid client settings; the default choice limit is60seconds. Saved preferences may contain other valid UI values without activating them.
- Practice mode awards no money or cards and restores original ownership through TCList with authoritative grown stats. Root commits the exposed stable original→updated snapshot to the economy; this module performs no inventory mutation itself. Reservation-backed selection validation should be wired in for native use; without the callback only structural validation is performed.
- Server combat, growth and enabled effects use recovered client formulas, including selector ties, visible battle ties, promotion thresholds and catalogue growth caps. Seed, raw/shared draw order, explicit board layout, CPU personality and scheduling are replacement-server policy.
- Display balance/CP and seeded PracticeStats are authenticated registration inputs. Result history/count/streak advance and are exposed for atomic profile persistence. Opt-in actual money credits and ordinary card prizes use the separate boundaries below. Ranked rating, historical hosted payout policy and event awards remain outside this route.
- Ordinary explicit Quit/room departure receives the corresponding completion reply. This module does not itself schedule disconnect settlement, watchdog deadlines or transport reconnection. The root server adapter separately owns its tested timeout/disconnect and session-generation behavior; do not interpret this module boundary as a whole-server limitation. Three-human ordinary matches are supported as documented below; three-player original computer menus remain guarded; ordinary three-player special boards and replacement AI are supported. Original computer menus/Conf1 are explicit opt-in functionality, documented separately; no native menu run is claimed. CBC gameplay is explicitly gated; award persistence belongs to the separate result-reward coordinator. Opt-in spectator projection/pacing belongs to the separate spectator service and does not establish native observation compatibility.
- Only single-body packets up to447 ASCII bytes are emitted. No guessed fragmentation format is used.

Validation: `python -B -m unittest test_match_service -v` from `server`: **102 tests**, including the computer-menu and three-player additions below. Coverage includes table start/settings, reservation callback boundary, malformed/replayed admission/start/abort/result controls, identity ownership, all-human turn pacing, direct capture, reciprocal combat, cascades, one-hop combos, loss behavior, exact battle values, perpetual-tie work bounds and cancellation, winner/defender promotion, duplicate identical copies, result growth/caps and stable final snapshots, seeded history/streak/counter persistence contract, original AI hand/placement/target integration, and32 complete seeded combat rounds across CPU and two-human modes. Existing whole-round/rematch/result-duplicate tests remain. Original PC instructions consume generated packets for all supported seat rotations; fixtures execute the tie retry loop with two generated BattleData packets and execute original hand reconstruction, per-slot growth and TCList on nontrivial placement order, confirming all grown fields and preserved deck assignments. These are emulator/module checks; no new desktop/native-game run was performed for this integration.

## Immutable observer boundary

`observer_snapshot()` returns frozen `ObserverSnapshot`/`ObserverPlayer` records without drawing RNG or acknowledging any turn. Call under the root adapter match lock. It contains phase, service-local round_id, sequence, active, copied player identity/rating/points/hand/hand_slots/selected, ordered game_rules pairs, configured start_fields, current immutable effect Cells, scores and turn_packet. Player peer is the same opaque authenticated identity token, not a copied mutable session. The packet is the exact most recently emitted TurnData for the active turn/battle sequence, including its original shared R; selection/result/lobby/finished snapshots expose None. Card and board tuples remain unchanged when the match advances. This is a state-copy API, not permission to bootstrap mid-battle or evidence of an observer presentation barrier. The recovered WatchInfo fields cannot represent every temporary property/modifier, so the observer adapter must decline unsupported projection states.

## Gated CBC reward boundary

`allow_cbc` defaults toFalse. Only the adapter that has staged result-reward persistence, overflow/Erase/Decks, Continue completion and interruption recovery should set it True. On an F18 battle, the service selects/generates one original catalogue pack75 card without a guild filter and includes full8 `/CBC=`. Its draws occur after shared BattleData R as explicit replacement-server policy. The original full receive/combat fixture consumes that generated packet and creates the matching base-owner5/current-active card. Generated deck association is255. Combat promotion uses the existing winner rule; result mode1 growth never touches these extras.

`FinalCards.extras` contains current-owner base-owner5 cards in ascending board order, independently of the immutable original5/updated5 replacement. The root first commits ordinary growth/statistics and then stages extras in the result-reward coordinator; do not merge extras into the five-card growth multiset or TCList. Repeated Ready/final snapshot reads do not regenerate or regrow extras, and Continue retains the snapshot until next complete selection. Evidence from the separate40-case `cbc_result_vectors.py` confirms both seat rotations, draw/nondraw paths and observer suppression: draw copies/clears extras during reconstruction; nondraw does so after ordinary hand growth. This module only exposes earned cards and does not itself commit or recover rewards.

## Three-human ordinary matches

`MatchService(mode='versus',player_count=3)` uses a5x5/25-cell board,15 turns,
and sequence0..14. Defaults remain player_count2. Public `player_count`,
`board_width`, `board_size`, and `last_sequence` let the adapter avoid hardcoded
two-seat limits. Registration and start require exactly the configured number
of distinct authenticated humans; every turn waits for all three acknowledgments.
The third seat has the same ownership, selection, battle-choice, Ready and
Continue checks. Rematch requires all three affirmative votes; any stop vote
finishes the round. Continue answers/B arrays and StartData L have three entries.

VsGameInit N3, StartData F25, TurnData score3,15-card TCList, result header14,
and per-recipient ResultData were executed through the full original receiver
at every local seat. The original result rule100CBD80 is
`class=2+count(higher scores)-count(lower scores)`; all136 distributions of15
played cards agree with original instructions. Mode1 growth uses class<3,
so middle class2 and tied-high class1 qualify while tied-low class3 does not.
The hosted no-stakes history policy maps class<2 to win, class2 to draw,
class>2 to loss; it keeps ranked awards/rating unchanged.

Each human still exposes one immutable five-slot final replacement for atomic
economy commit. The15-card TCList's worst-case426-byte body fits the proven447
limit. Worst-case three-seat PLAYACK is432 bytes. ResultData sends the receiver's
full D/T/RN fields and neutral complete D tuples containing each remote rating;
the original remote branch reads only D2. This avoids oversized multi-profile
packets and preserves each recipient's complete local fields without inventing
fragmentation. All three bodies remain within447 at maximum field widths.

Five new MatchService groups cover these bounds, all3 seat rotations,136
original ranks, six complete fifteen-turn rounds including combat and rematch,
third-ack buffering, final-snapshot lifetime, and Ready/Continue deduplication.
ResultStats and ResultRewardService now accept seat2, including authenticated
result edits and durable reward recovery; seat3 is rejected. Economy participant
atomicity and transport/directory capacity are separate adapter-owned tests.
Three-player CPU/menu selection and explicitly supplied nonzero25-cell F remain
guarded. Generated three-player special boards are supported by the original
ordinary generator as documented below. No native three-player display
or full UI behavior is claimed by these headless tests.

## Opt-in ordinary card prizes

MatchService(card_prizes=True) integrates the separately tested PrizeLedger for
two or three humans or a human/computer pair. DefaultFalse preserves practice
ownership. Original computer-menu and versus-shaped CPU prizes use the proved
uniform eligible-card chooser and the delivery boundary described below.
TCList exposes original captured owners and grown cards in board order;43/0F
accepts only the current winning participant's eligible compacted hand index.
Sole-candidate/perfect awards are automatic. Stable original selection slots
survive all compaction and deck255 conversion.

The service exposes prize_choice, final_prizes()->FinalPrizes(round_id,settlement),
prize_commit_required and confirm_prize_commit(round_id). Automatic-complete
rounds buffer ResultData; ambiguous rounds buffer the final GetSelect reply.
Root must commit existing growth/statistics, ordinary transfers and combined
CBC/prize staging durably before confirmation releases those messages once.
final_cards remains available while delivery is held. Continue rejects until
ownership settlement is confirmed. Final snapshots survive Continue until
next complete selection. Root owns interruption recovery and must wire it
before enabling the flag. No wager is enabled by this option.

MATCH_PRIZES_NOTES.md details exact source blocks, integration commit order and
the original wire's lack of a prize-choice generation field. Five new service
test groups and six standalone prize tests pass, including500 complete original
instruction comparisons, both native-parser seat rotations, three-player
compaction, duplicate/stale rejection, CBC separation and persistence barriers.

For an ambiguous computer win,100CA258..100CA284 consumes a pacing draw and
then uniformly selects from eligible compacted hand indices. The adapter uses
denominator1 scheduling and retains both original draws; it applies no invented
rarity/stat preference. This resolves before FinalPrizes, so ResultData stays
held until durable ownership confirmation. The human's first Ready then receives
TCList followed by the cached computer GetSelect. Sole/perfect awards require
neither a choice draw nor GetSelect. Human winners keep the existing request and
final-reply barrier.419 original choice cases and the original queuedTCList/0F
consumer sequence pass; four additional service groups cover both computer
representations, human wins, duplicate Ready and menu/rematch reset.

## Opt-in wagers and actual result credits

`allow_wagers=True` requires both synchronous economic callbacks. Defaults remain
zero wager/double-up and zero rewards. The hooks operate on authenticated human
seats; a computer seat never becomes an economy account. A callback exception
produces no successful acceptance. Root must recover/abort its durable transaction
on failure; the service does not invent a wire error or silently skip a debit.

```python
def round_authorizer(match, next_round_id, stake):
    # Use a durable ID including match instance + next_round_id.
    # MatchEconomy.begin(..., stake, mode=match.mode,
    #                    player_count=match.player_count)
    # Atomically persist escrow and refreshed account reservations.
    return {human_seat: post_debit_balance, ...}

def payout_provider(match, round_id, scores, ratings):
    # Read-only MatchEconomy.quote(...); do not credit funds here.
    return {human_seat: actual_nonnegative_credit, ...}
```

The authorization callback receives exactly the next service-local round number,
`completed_matches+1`, and the proposed stake explicitly. For accepted computer
configuration/rematch, `game_rules` still describes the prior configuration while
the callback runs: use the `stake` argument. Every human's post-debit balance must
be returned as an integer0..99,999,999. Local affordability is checked before the
callback; the economic authority must independently validate its reserved account.
Successful authorization stores `authorized_round_id` and frozen `round_stake`.
An unchanged successful round is never authorized twice. Reset clears these
fields; the adapter should normally create a new service for new membership.

For ordinary tables,14 merely validates and stores the latest settings. `begin`
authorizes once after every required participant accepts, **before** constructing
VsGameInit. Its `/M` fields therefore contain post-debit balances; the receiver
overwrites its local balance and does not subtract again (`101028C0..CE`). The
original owner bypasses admission debit on GameEA EN1 (`10090114..143`), edits
the wager, then locally subtracts the chosen value immediately before sending14
(`10052A02..10052A26`, send`10053491`). Existing-table EN2 joins subtract the
cached wager at`10090145..15C`. Uniform durable debit at server start is explicit
replacement policy; it avoids charging the owner before its actual choice.

The original computer menu's ComGameInit `/M` is pre-debit. Rule-only06/header0
updates settings without authorization or RNG. Fully validated06/header2 authorizes
its final wager before changing configuration, generating the NPC hand or entering
selection; the client has already subtracted this choice at`100EC1E2..1F3`. No
StartData is emitted until CardSelect, preserving the existing queue boundary.

Continue computes `min(2000, previous_bm*(1+du))`, matching both original dialog
constructors`1010BC4B..77`/`1010BF5C..88`. It authorizes only after **all** humans
vote yes, before the positive Continue packet. A partial vote, repeated vote,
stop vote or failed authorization never charges or changes the active wager.
The original rematch UI performs its own displayed debit at`100BEFE9..100BF001`.
Computer Conf1 returns ComList and updates the next displayed doubled/capped
wager without charging; the next final06 selection authorizes its actual wager.
Conf0 keeps the chosen NPC and authorizes before regenerating its hand.

At result, the read-only payout callback receives final scores/ratings before
stats advance or packets are built. It must return exactly every human seat,
with credits bounded by99,999,999 minus that seat's post-stake balance. The service
invokes `next_result(outcome,reward,fully_tied=(len(set(scores))==1))` once, writes
the actual credit to D1, updates its displayed balance once, and freezes the same
stats in FinalCards. A three-player middle finish can have outcome0 without a
fully tied board: it still includes an actual reward in cumulative sum/maximum.
A fully tied refund is sent in D1 but excluded from those reward aggregates.
Only positive credits belong in D1; negative debit encoding is never used.

Before releasing ResultData, root must pass the quoted account-to-credit mapping
to `EconomyService.commit_match_growth(...,payouts_by_key=...)`, then commit the
matching money with MatchEconomy.record_result and persist both atomically. The
existing prize barrier remains separate: automatic-complete rounds may additionally
hold ResultData, and ambiguous prizes hold the last GetSelect. Immutable snapshots
and repeated Ready/Continue do not quote, grow or credit a completed result again.

The payout formula belongs to MatchEconomy: versus uses the documented local
zero-sum pot policy; computer uses the recovered **offline** arithmetic. Neither
claims the old hosted service's monetary/rating policy. Stakes and money are
independent of `card_prizes`; enabling one does not silently enable the other.

Ten new hook tests cover first-owner versus joining-player EN, latest settings,
exact callback boundaries, bad mappings/amounts, affordability and persistence
failures, all-vote double-up, stop/retry behavior, computer06/Conf1/Conf0, actual
three-player middle rewards and duplicate result requests. Original instructions
consume the generated wager/balance init and D1 packets and verify the double-up
cap. No native process or desktop use was involved.


## Native table-password compatibility

Active primary14 accepts a password only when the complete settings can be
encoded faithfully by table_metadata.table_info before any mutation. The native
editor's twelve-byte pw input exceeds the eight-byte TD/PTL field; nine/twelve-byte
active values, embedded nonpadding asterisks and PTL/IRC framing bytes are rejected.
Valid padded values remain unchanged in rules_body, Rule13 and PLAYACK. Saved24
preferences can retain a valid twelve-byte editor value without activating it.
Rank bounds remain the original advertised au300/al100.

The password is advertised in table metadata, not a server secret. The original
client compares entered text case-sensitively against the cached table password
at10049742..100497EE before its local admission action. GameEN/Req carries no
password proof, so this compatibility feature does not provide server-side
password authentication against a modified client. Parent's20 original cases
verify the local comparison and events. Two MatchService groups verify preserved
padded output, metadata limits, framing rejection and atomic settings behavior.


## Boards generated from accepted rule toggles

Without an explicit start_fields override, accepted st/cb/ca flags now drive
native_board_generation at the completed CardSelect boundary. These toggles
need not describe a preconstructed field array. The helper exactly ports original
100C0F16..100C11E5, including literal count tables, branch-dependent random draws,
placement without replacement, initial active seat and the initial eight shared
bytes. StartData F/B/E/S and the first TurnData A/R agree with that output.
Subsequent rounds regenerate from the continuing stream. Explicit overrides keep
their existing flag validation and S0 schedule; the all-zero default retains the
previous empty board. Original offline generation with flags0 also creates walls;
that deliberate default-policy difference is documented rather than hidden.

The ordinary generator emits F0..17 only, so enabled cb does not silently enable
CBC rewards. Counts preserve at least five placement cells per participant.
Generated4x4 CPU/versus and5x5 human games execute through the existing verified
special-effect engine. NATIVE_BOARD_GENERATION_NOTES.md records every table,
address, count distribution and scheduling boundary.1,024 original executions
match every result and raw draw. Four new service groups check actual original
receivers at every seat,64 full human matches,12 CPU/menu rounds with rematch,
and unchanged default/explicit draw schedules.


## Departure, rating hook, and packet capacity follow-up

`allow_departures=False` is a separate explicit engine gate. Opt-in ordinary
unrated/no-prize/no-wager games use the original gs0/1/2 transitions through
`departure_supported(peer)` and `depart(peer,replacement=1)`. Root owns disconnect
triggers and must retain departed account reservations while live play continues.
Selected-five snapshots remain complete even when native result hands are partial.
NATIVE_DEPARTURE_NOTES.md records exact transitions, original comparisons, the
Ready10/15 correction and unresolved voluntary-exit economic settlement.

Optional `rating_provider(match, completed_matches+1, tuple(scores))` runs once
before result statistics advance. It may return None to preserve ratings or an
exact mapping of every human seat to an immutable original RatingQuote. Each
quote is validated by PracticeStats.next_result(rating_change=quote); CPU ratings
remain unchanged. Player.rating, ResultData D2 and FinalCards.stats use the same
new value. Root owns authenticated quote identity and atomic history/profile
commit. Rated departures are deliberately declined before mutation.

Registration and active settings now preflight the complete PLAYACK with the
existing447-byte textual-body limit before mutation. No member field is omitted,
truncated or split into unproven fragments. Collection CP is a WORD and is bounded
accordingly. Three maximum-length15-byte names,64-bit IDs,CP65535, original
rating300, all maximum representable settings and an8-byte password fit. A legacy
combination with three10-digit ratings that exceeds447 rejects atomically; values
are not silently clipped to make the envelope fit.
