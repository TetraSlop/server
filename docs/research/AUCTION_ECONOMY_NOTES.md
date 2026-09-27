# Auction escrow and durable collection

`auction_economy.py` implements synchronous account/ledger transitions. Persist its `snapshot()` as `auction` in the **same atomic JSON document** as `EconomyService.snapshot()`. Restore accounts first, then `AuctionEconomy.restore(economy, snapshot.get('auction'))`; absence supports old saves. No intermediate response may be sent before that joint persistence succeeds. In-process reservations are deliberately excluded from saves.

## Recovered constraints and explicit local policy

The original exhibit path `1012D820/101305C0` supplies opening bid, increase, duration, reoffer count and a 32-byte descriptor. The UI permits increases20/50/100/200/500/1000, duration1..168 hours and reoffers0..3. It blocks at30 active exhibits or five retained cards. Positive exhibit acknowledgement removes one local card (`1012D115..1012D13E`). Positive bid acknowledgement deducts the **full** submitted amount (`10126012..10126033`), with refunds collected separately. These contracts drive escrow validation; client inputs never grant ownership or money.

Local policy is **zero fees**, a caller-supplied Unix-second clock, opening bid>=1, scheduled expiry, the requested number of automatic unsold reoffers, and no seller self-bids. An outbid amount becomes a durable refund. A bidder raising its own bid also deposits the full new amount and receives its prior deposit through the refund queue. Expiry moves the winning escrow to seller payment and the card to the winner's pending queue; unsold final expiry returns the card. These are explicit local settlement choices, not claims about the historical server.

`BC` is the local successful-bid revision/count. It starts0 and changes only on a successful bid. The original listing UI uses nonzero BC to display the bidder, so automatic unsold reoffers retain0. A bid must quote the current revision; an old quote cannot overwrite another bidder. Listing `minimum_bid` is opening bid before the first bid, then current bid+increase. The wire adapter separately documents its native quote projection.

`cancel()` is a trusted administrative API for an active, unbid, owned listing. No native cancellation message was found; this API must not be assigned an invented opcode. Cancellation queues a returned card rather than silently adding it to a live client's inventory.

The descriptor uses `[model,value,RA,zero,count,language,CP,zero,attack,physical,type,magic,points,arrows,zero,unknown]`. The ledger validates widths, the owned card's complete attributes and original catalogue sale value. Display metadata does not authorize transactions. This wire omits a unique card instance and deck association; indistinguishable copies use the same stable-first matching policy as ordinary inventory edits. Escrow cards have deck255.

## Collection API

`reserve_collection(key)` creates an exclusive account reservation and freezes the pending IDs visible to that session. It exposes initial account state, payment/refund totals, and at most30 returned and30 won cards. Later settlement/refund entries wait for a subsequent session.

1. `commit_payment(reservation)` credits available money and returns the actual amount to emit. Persist before B2/20 transmission; original `101348F0` has no money acknowledgement.
2. `commit_returned(reservation, cards=None, deck_names=None)` consumes the first card stage only at its Get, including an empty stage. The default accepts all returned cards.
3. `commit_purchased(...)` similarly consumes the winning-card stage at the second Get.
4. `commit_refund(reservation)` credits refunds and releases the account. Persist before emitting this amount and terminal Quit.

`collection_inventory(reservation)` supplies the current committed inventory plus the current incoming batch. An optional complete `cards` tuple incorporates explicit client Erase/Decks choices. The economy boundary checks unchanged card attributes, owned multiplicities, capacity1000, unique deck slots and names. It allows explicitly selected discards but no manufactured cards. Overflow must be resolved before Get can commit. The two Get messages have identical wire contents; ordered transport and phase gates are required. There is no invented per-stage idempotency token.

`release_collection()` drops only the live reservation. Unaccepted cards and refunds remain pending. Money already committed before transmission stays credited, and a fresh authoritative profile makes reconnect coherent. Root/wire integration must close/resynchronize after transport failure; it must not try to reconstruct delivery from a client's possibly partial local state.

Native payment arithmetic clamps at99,999,999. The local ledger emits only the amount that fits and **retains the residual pending amount**, avoiding destruction of escrow. A full balance can therefore leave a pending probe present until the player makes room. Money, cards and pending entries remain conserved except explicit client card discards. Auction proceeds do not increment match prize totals or rating.

## Economy boundary and checks

`reserve_auction`, `commit_auction` and `release_auction` are public EconomyService capabilities. They exclude shop, trade, match and other auction mutations; a successful staged commit refreshes the capability so an old token cannot replay. The trusted ledger proves the source of added escrow cards and credited money. The economy layer independently validates removal ownership, balance limits, card stats, capacity and deck consistency. Option edits remain permitted and preserved. There are no callbacks or awaits between account and ledger assignments.

`test_auction_economy.py`: **22 tests**, including eight fresh original-x86 comparisons of both money child commits and both card child commits (`101348F0`, `10135920`, `10134F00`, `10135410`). UI/transport/refresh plumbing is stubbed; original arithmetic, destructive card move and Get emission execute. The tests cover two-bidder escrow conservation, self-raise refund, expiry/reoffers, stale bids, forged descriptors, busy accounts, empty ordered stages, disconnect before/after Get, money-before-send recovery, residual cap credit, overflow rejection/discard, deck/options preservation, batches exceeding30, concurrent pending additions, snapshot corruption and legacy saves.

Combined auction/economy/trade/match-economy/result checks: **100 passed**. The inherited external binary reader emits a ResourceWarning; no external code was edited. No native game, GUI or historical service was launched. Full historical auction policy, timestamp epoch, fee behavior and unused fields remain outside what the original client proves.

### Registration after an auction

Auction actors retain the guild they had when listing or bidding. An account
may subsequently register from guild0 into guild1..3 through Init80. Restore
accepts that one-way transition while retaining the historical record; an
already registered actor must still match the current guild. Account key, NN,
character ID and name remain authenticated, and saved guilds must be integers
in0..3. Comparing the old guild directly with the current one previously made
an otherwise valid acknowledged registration impossible to reload.

Three new regressions cover all guild choices, historical seller/current bidder/
bid history, impossible transitions and forged identities. The encrypted case
lists and bids before registration, reloads after each Init80 acknowledgement,
then raises the bid and reloads mixed historical guild values. The focused
auction/profile/claim batch passes64 tests without launching the native game.
