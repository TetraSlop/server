# Local auction service

`auction_service.py` projects `AuctionEconomy` into the recovered original PC
messages and binary files. It performs no socket, disk, timer or GUI operations.
`test_auction_service.py` currently has 18 passing tests, six of which execute
original client instructions using the existing Unicorn harness. No native
auction session has been completed.

## Integration boundary

```python
auction = AuctionService(auction_ledger, clock=auction_clock)
replies = auction.handle(authenticated_account_key, payload)
data = auction.file(authenticated_account_key, path)
recognized = auction.delete(authenticated_account_key, path)
auction.collection_active(authenticated_account_key)
auction.disconnect(authenticated_account_key)
```

`handle` returns `None` for another protocol, an empty list for accepted edits,
or ordered response payloads. Malformed identities, field widths, stale
collection sessions and out-of-order collection operations raise
`EconomyError`; invalid BEL syntax raises `ValueError`. Mapped auction economic
failures use EF/BF with the ledger's error code. Caller closes/resynchronizes a
malformed session instead of claiming success.

Call `advance_auctions()` before auction requests/collection entry and in the
server's existing periodic task. The service's file provider and queries do not
advance settlement. Persist the joint account/auction snapshot atomically
**before sending mutation replies**, including collection entry's money credit.
The ledger's `snapshot()` excludes reservations but retains committed payments
and all still-pending deliveries. On disconnect call `auction.disconnect(key)`
before another service acquires the account. Queries never reserve an account.

Route GTM0A separately from ordinary B2 shop traffic. Query replies use the
addressed auction service identity. B0 subtype01 `ShReq` enters collection at
the taught Shm identity (currently SHOP_RAW); subsequent B2 replies must come
from that same identity. The original Init stores it in player+28/+2C and later
deliveries verify both sender and session. B0/00 belongs to the ordinary shop;
B2 belongs to this module only while `collection_active(key)` is true.

Collection session tokens are account-scoped, advance through1..255, and are
ephemeral. The constructor of the original collection scene drains old
43/B2/A2 queues. There is no recovered resume or idempotent retry transaction ID.
The two identical ordered Get messages cannot distinguish a duplicate from the
next acceptance; do not add automatic application-level retransmission.

## Implemented messages and files

* ER(actor),SP,BI,ET,AM,II → ES plus rich listing fields, or EF.
* BR(actor),AI,BM,BC → BS plus rich listing fields, or BF.
* **SI**(actor),filters → SS(filename),SN(count), or SF.
* **BH**(actor),filters → HS(filename),SN(count), or HF.

The twelve actor fields are authenticated against the account's game identity,
character ID and name. IO/IB name, AI ID, MP price range, LT remaining-hour range
and repeated IC selectors0/model and10/type are supported. Unsupported selector
or catalogue-only tokens are rejected instead of assigned invented behavior.
Names compare exactly; prices and integer remaining hours use inclusive bounds.

Exhibit records are C0 bytes, bid records48 bytes, and `b/g/TM0AucData` is
**20 bytes**, five little-endian counts for all,0..1000,1001..2000,2001..5000,
and greater-than5000. Search responses name immutable, account-scoped
`U/g/TM0_EXHIBITLIST_XXXXXXXX` and `U/g/TM0_BIDLIST_XXXXXXXX` files. Later bids,
settlements and searches do not alter a published snapshot. Exact deletion is
idempotent. Deleting the original canonical filename clears only that account's
corresponding namespace. The shared account cap is64 snapshots, with no silent
eviction. A matching result above1000 records fails rather than truncates.

Pending `u/g/TM0_PM`, `u/g/TM0_BI`, `u/g/TM0_DI`, `u/g/TM0_RM` file reads return
one zero byte when any delivery is pending, otherwise absent. Original fixtures
show these files are tested only for successful read and no content bytes are
consumed. Publishing all four availability markers is a local policy; it does
not claim historical BI/DI suffix semantics or fabricate a binary record layout.

## Collection and a newly verified Init detail

The original B2/13 normal Init **does not use its header as the session**.
Body `/N` establishes the session. At10105390..101053AB, header value1 makes
the receiver return-1; headers0,2,17,255 return+1. The new test executes all five
cases with body `/N=93`, and all store93. The service therefore sends success
header0 with `/N=allocated_session`, and uses that session in subsequent
Data/Card/Quit messages. This distinction was exposed by running the actual
entry receiver against the first allocated session1.

1. B0/01 ShReq validates NN/HID/Dm/Vol/CN. Acquire the collection reservation.
2. Commit payment to the account and remove those pending payment IDs before
   sending Init, B2/20 `Data M=actual_credit`, and B2/21 returned-card pages.
3. First B2/15 Get commits returned cards plus staged Erase/Decks changes and
   sends successful-bid card pages.
4. Second Get commits purchased cards plus edits, credits/removes refundable
   money, releases the reservation, and sends Data followed by B2/17 Quit D=0.

All four stages occur even when empty. Money stages use M=0 and card stages
use E=0/S=0/C=0. Card pages use reduced6 tuples and fit the native447-byte body;
the total per card stage is at most30. Each page restarts its Nk numbering.
Only the two card stages have acknowledgements. The server's terminal Quit is
proactive; waiting for a client Quit would deadlock the original parent scene.

Collection Erase Mode1 and Decks edits are staged until that card stage's Get.
Mode2 is a completion marker. Stable multiset matching preserves duplicate-card
counts and full inventory cannot commit until the client resolves overflow.
Disconnect discards unaccepted edits/cards and keeps those deliveries pending.
Already emitted money remains durable because no money acknowledgement exists;
a fresh profile load is the recovery boundary if delivery fails after credit.

## Explicit local policy and remaining limits

Economic fees, escrow, reoffer and settlement policy belong to
`auction_economy.py`; they are not claimed historical behavior. BC is the local
successful-bid revision, initially0 and unchanged on unbid reoffers. Original
10121F34 onward uses BC>0 to show a bidder name, so an unbid listing stays0.

NC at record+48 is the expiration clock: original101221CF..10122226 obtains it
and the following duration calculation subtracts the current clock. This server
uses Unix seconds consistently with its network clock. ED is created time, LC
last bid or created time, and CD last bid time as explicit local projections;
full historical ED/LC meaning remains unresolved. Bid-history+38 is projected
as auction ID, +44 is zero. Unused actor metadata is zero, with identity/name
retained, and display names always have a NUL within their16-byte field.

For an unbid listing CM is its opening quote. The native UI suggests CM+BI, so
its first suggested bid is opening plus increment; the ledger still accepts an
explicit opening bid. This is a visible local quote policy, not a claim that
the retail server used this representation. Metadata and MP searches use the
same quote. Read-only search ordering is listing ID ascending or historical
bid amount descending with stable ties.

Original tests cover actor/item serializers, rich ES/BS parser output byte for
byte against binary records, pending SS/HS callback and reader offsets,
Init/source/session establishment, empty money/card children, thirty-card
pagination and terminal Quit. The adapter does not implement any fabricated
auction cancel message. End-to-end native auction UI interaction remains a
user-driven validation task; no game was launched for these tests.
