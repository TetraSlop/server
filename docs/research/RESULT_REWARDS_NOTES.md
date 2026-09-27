# Generated CBC cards and result acceptance

`result_rewards.py` supplies the durable inventory portion of the F18 materialization tile. MatchService owns the original special effect, pack75 generation, BattleData/CBC and final current ownership. Its `FinalCards.extras` contains generated cards in board-index order, separate from the player's original five cards. This module does not create opponents, player-to-player stakes, ranked money or prize points.

## Original client contract

The special tile at100D220C copies the supplied CBC card and sets **base owner5**, current owner to the actor and property0. Subsequent combat can transfer its current owner and apply ordinary combat type promotion. It never acquires an original selected-hand slot.

At result sub35,100C881B..8864 reconstructs only base owners less than player count into the five-card hands. Base-owner5 cards take a separate path:

- For draw flag G+189==0,100C8869..8898 exports each card whose current owner is local0 to P+4B90 and increments P+F0. The card is then cleared.
- For nonzero result flags, the generated board card is retained through ordinary hand growth. At100C9039..9086 it is exported if current owner is local0, then cleared regardless of owner.
- The ordinary mode1 result-growth loop at100C8F43..8FAD processes reconstructed player hands. CBC cards do **not** receive that growth. Their final existing stats, including earlier combat promotion, copy unchanged.
- Observer/special scene flag G+197 bypasses this ordinary result export. The hosted feature is limited to the two-player ordinary result path.

`cbc_result_vectors.py/json` contains **40 passing original-instruction executions** across both local seats, both current owners, draw/result flags1..4, the terminal growth branch and the observer bypass. Original compact export and clearing execute. No rendering, native process or network is used. Generated cards never enter ordinary hand counts in these fixtures.

CBC therefore needs no player-to-player GetSelect. The existing no-stakes TCList can continue returning each original player's five cards while owner5 gains proceed independently. The native result merge accepts pending gains at100CB7F8..854. Overflow uses context3 Erase43/10; it does **not** send shop Get. Continue10107CAB flushes attached Decks and Erase editors. Header and `/P` are the local server seat for these ordinary-game edits.

## Integration and atomic state

Create `ResultRewardService(economy,auction)`; persist its `snapshot()` under top-level `result_rewards` in the same atomic JSON as accounts, auction and prize state.

1. Commit original-five growth and result statistics through the existing `commit_match_growth` API.
2. For every human result, including empty extras, call `stage(key,refreshed_match_token,reward_id,seat,extras)`. Use a server-generated table-instance UUID plus round/account in the stable ID; a restarted table's round1 must not reuse another round's ID. Persist before ResultData/TCList.
3. Route43/10 Erase and43/22 Decks to `handle(key,payload)`. It validates authenticated seat, owned multiplicities, stats and names, and stages edits against committed inventory plus extras. Mode2 only marks completion. It never deletes all cards.
4. Validate Continue through the match engine, then call `commit(key)` **before emitting its response or permitting rematch**. This commits retained generated cards and explicit discards/deck edits, checks capacity1000 and returns a refreshed MatchReservation. Replace the adapter's held token. Existing result statistics, money and prize points remain unchanged.

The public economy methods `match_account` and `commit_match_rewards` avoid private account mutation. The trusted engine proves generated-card provenance; the economy independently prevents extra manufactured cards, changed card stats, invalid deck associations, stale tokens and capacity violations. Batches are bounded30, matching the native incoming buffer. This is a local transaction boundary inferred from the visible Continue flush, not a recovered historical database commit point.

Repeated staging of the same durable ID/content is a no-op; a different payload with that ID is rejected. A completed Continue retry cannot apply the extras twice. No client-generated ID is trusted. Empty-extra results still admit legitimate post-result Decks/Erase but produce no empty pending collection marker on recovery.

## Interrupted result recovery

An unaccepted generated card must not silently disappear, and a1001-card save cannot represent it. The local recovery policy uses an **existing native path**, auction collection's Returned Card stage, with no fee or money change. This does not claim the original server used auction storage for match rewards.

On disconnect/table abandonment call `recover(key)` before releasing the table account. It imports the full earned batch through `AuctionEconomy.enqueue_returned(key,source_id,cards)`, then removes the pending result. Both ledgers must be persisted atomically. The source ID is deterministic from account and reward ID; repeated imports, including after claim/restart, cannot create duplicates. An active auction collection keeps its frozen pending IDs and is not invalidated; newly imported cards wait for the next session. Collection delivers at most30 cards per stage, leaving excess queued.

At startup restore all ledgers, call `recover_all()`, then save the joint state before serving clients. Restored pending results are recovery-only: there is no invented native result-scene resumption. Unaccepted Erase/Decks edits roll back to committed inventory; original earned extras survive in the Returned Card queue. The player can collect them through the normal auction collection scene, including its actual overflow/discard/Get flow. Already-accepted results do not enter recovery. The card's UI collection label remains Returned Card; this recovery UX is explicitly recorded for later review.

## Detached deck renames

Rename can clear P+1C before its detached editor sends, so Continue's pointer flush is not a universal final-edit barrier. After acceptance, `handle` admits only delayed Decks. With a live table token, it refreshes that token; copy `reservation(key)` back to the adapter. After immediate table-token release, call `release_context(key)` to retain only the exact immutable accepted account view. Late Decks then uses public `apply_deck_changes`, only while the account is idle and still equals that view. Any changed balance/inventory or reservation invalidates the context. Erase is never accepted after Continue.

Call `forget(key)` on disconnect, new table admission or new CardSelect. This prevents an old result editor from changing a new match's selected-card baseline. `recover(key)` without a pending result is a no-op and preserves accepted context; cleanup chooses explicitly whether to retain it until the expected detached editor tick or forget it.

For an accepted context still attached to the same table, a new rematch stake or an abort refund changes only money and refreshes the MatchReservation. Call `refresh_reservation(key,new_token)` before handling another Decks or releasing the context. It verifies the same account, cards, deck slots/names and canonical profile bytes, masking only the four balance bytes at34. No accepted context returnsFalse; a pending/released context or any other mutation is rejected. Legitimate unrelated profile/option changes make the old editor context obsolete: the adapter may forget that accepted context rather than keeping a stale capability. This API does not accept a grant or resume a released editor.

Three added unit tests cover stake/refund rebinding, late deck mutation, foreign tokens, inventory/profile changes and pending/released-context rejection. Twelve independent encrypted edge tests in test_match_prize_edges.py pass, including full rounds with CBC plus ordinary prizes, interrupted choices, post-save delivery failure,1000-card Erase/Continue, underfunded wagers, failed next-stake saves and late Decks after a paid rematch. Those tests found and verified a root cleanup fix: a transient finished phase still holding reservations must reconnect every participant if result acceptance fails.

## Validation and limits

`test_result_rewards.py`: **19 tests**. They cover fitting acceptance, capacity/discard, unchanged stats/points/money, duplicate reward IDs, forged ownership, empty extras, side authentication, names/options, delayed live and released-token edits, rollback and recovery after restart, full-inventory recovery through collection, an active collection receiving later queued rewards,30+2 batches, two-account isolation, corrupted saves and eight fresh original owner5 export comparisons. The separate original fixture records40 cases.

Combined result-reward/auction/prize/economy/match-economy checks: **101 passed**. Root owns encrypted end-to-end tests and feature enablement. These APIs implement automatically earned CBC cards; they do not enable player-to-player prize selection, wagers, ranked awards, three-player results or historical reconnect rules.
