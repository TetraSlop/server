# Prize Center service

`PrizeService(economy, seed=None, raw_rng=None)` implements the recovered **70 CvReq →71 CvEnter +B2 ExcInit →Buy45..74 →Card →Get →Quit** flow. `handle(authenticated_key,payload)` returns a list of reply payloads, or None for unrelated traffic. Route70 to this service, then route B2 to it while `is_active(key)`. Ordinary shop and auction B2 routing must not also consume an active prize session. `disconnect(key)` releases its reservation and discards an unaccepted purchase; the client must load its authoritative profile after reconnect.

After Quit, `can_replay_quit(key,payload)` recognizes only the same account's
completed session, Quit opcode and exact `Quit/No` fields while its account is
idle. The adapter routes this receipt through the usual prize transaction before
replying. Active shops and auction collections retain priority even when their
independent session counters happen to match. Replaying Quit changes neither
cards, points, money nor durable journals. The receipt is connection-local:
every disconnect clears it, including after the visit has already ended, and a
restart never restores it. Other messages cannot reopen a completed visit or
use its receipt to authorize an inventory edit.

Persist `snapshot()` under top-level `prize` in the same atomic document as the economy and auction ledger **before sending replies**. Restore with `PrizeService.restore(economy,saved.get('prize'))`; absence accepts old saves. Snapshot data contains award IDs, unredeemed awards and the last session per account. In-flight purchases are intentionally absent. The server owns network persistence and fail-closed error handling.

## What is recovered

`CvReq` has NN/HID/Dm/Vol/CN; NN and name must match the authenticated account. `ExcInit` PP order is **current points | Top30 | Lucky | Rookies**, followed by three LC model IDs. Original101055F1 treats initialization header1 as failure; /N supplies the actual session. The service emits header0 even for session1. Four original receiver comparisons cover header0/1/2/255. The same issue was corrected in ordinary EconomyService._init, with another four receiver comparisons.

Original10178ECB adds Top30+Rookies,10178F14 adds Lucky, both unsigned-clamp to99,999 and send no acknowledgement. The authoritative point balance is the DWORD at **TM0DataFile+134**, separate from money+34, rating+30 and RT+128. The public `prize_points`, `reserve_prize`, `commit_prize`, `release_prize` economy API preserves those independent fields, card ownership, options and deck names. A reservation excludes shop, match, trade and auction changes; capabilities refresh at commit and cannot replay.

Pack IDs45..74 come directly from decoded PackPrm. All30 contain one explicitly selected model; prices are50 through10,000, exactly the catalogue values. The service uses the existing recovered card selector/generator and a supplied or local raw RNG. Recovered generation does not establish the historical online server's random stream. Original state20 debits prize points after positive Card at10179338, then fitting ownership moves at1017961D and Get sends at10179657. The server stages the generated card, point debit and any Erase/Decks edits until Get. Money remains unchanged. Buy retries replay the exact pending card; a different Buy while pending receives Card/S=0. Bad/unaffordable prize IDs also receive Card/S=0.

Capacity1000 is enforced at commit. The1030-slot native overflow workflow is represented by the one extra pending card and bounded Erase/Decks records. Mode1 explicitly discards matching owned copies; Mode2 is only the native completion marker. Deck assignments and names can arrive before Get or later while the session remains active. Get has no reply; a completed Get retry changes nothing. Quit cannot accept an uncommitted purchase. An ordinary Sell is not admitted through Prize Center.

## Explicit local award policy

No ranking or Lucky Card award is invented automatically. New accounts and an empty award ledger receive all-zero award fields. `grant_award(key,stable_id,top30=0,lucky=0,rookies=0,lucky_cards=(65535,65535,65535))` is a **trusted policy/administration API**, not a network command. Each amount must fit the original WORD, and LC entries must be catalogue models or the no-card sentinel. One pending bundle per account keeps the three award amounts and LC coherent. Known stable IDs remain recorded after redemption, so duplicate grants/restarts cannot award twice. Awards queued during an active visit wait for a subsequent entry.

At entry, the service atomically credits the award total, applies the original99,999 cap and consumes the pending bundle **before ExcInit is sent**. The reply carries the pre-award point balance and award fields, so the client's local announcements arrive at exactly the already-saved balance. There is no native award acknowledgement to await. Repeated idle entry requests replay the cached initialization without another server grant; initialization resets the local base before applying the same announcement. After a successful purchase, that cache is refreshed with the current point balance and zero awards, so an entry retry cannot restore spent points. A disconnect before the announcement preserves the already-granted points; the next entry sends those saved points and no duplicate award.

The original RankingPrize display table is mapped separately, but weekly eligibility, award scheduling, ties and Lucky Card selection remain server policies requiring an explicit future choice. This service does not claim to recreate them. Award overflow follows the original clamp; unlike auction escrow residuals, excess awarded points are not retained.

## Validation

`test_prize_service.py` covers all30 original prize models/prices, independent money/point balances, idempotent awards across retries/restart, queued awards, maximum cap, unaccepted rollback, accepted reload, session wrap and authentication, full inventory/discards, forged edits, delayed deck edits, options, busy-account exclusion and corrupted snapshots.

Closed-Quit coverage additionally checks exact receipt matching and disconnect
cleanup. Encrypted integration tests replay a completed purchase's Quit through
both the taught shop and room routes, verify byte-identical durable state, and
exercise colliding active shop/auction session numbers and a fresh reconnect.

Fresh original-instruction comparisons include all30 price-debit blocks, four ExcInit status variants and one integrated generated **ExcInit →awards →Card →point debit →card move →Get** sequence. Original points, money, count, acquired model and outgoing Get agree with the service. UI/transport/allocator plumbing is substituted; this is not a native graphical session.

Combined prize/economy/auction/trade/match-economy/result checks: **118 passed**. Original source repositories and installed game files remained read-only. Root owns the encrypted network integration and joint disk persistence.
