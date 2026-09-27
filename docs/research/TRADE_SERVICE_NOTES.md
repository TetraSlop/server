# Local player trade service

`trade_service.py` implements the mapped **two-player A2 card exchange**. It does not describe an NPC recipe system. The Prize Center is the distinct B2 `ExcInit`/`Buy` flow in `protocol_map/PRIZE_CENTER.md`.

The implementation uses `SHOP_SEQUENCE.md`, `SHOP_TRADE_EXECUTION.md`, `INVENTORY_BATCHING.md`, and `TRADE_CONTROL_SEQUENCE.md`. The client supplies offers, selections and readiness; the local server checks owned copies and coordinates exchange. The historical server's commit, timeout, entitlement and rollback policies remain unknown. Policies below are explicit choices for this local server.

## Integration API and identity

Create `TradeService(economy)` once. Bind each authenticated connection with `bind(peer, account_key)`. A peer is an opaque hashable connection object, not a nickname parsed from the request. Each account may have only one bound connection. Account identities come from `economy.account_view(key)`.

After the adapter has observed an authenticated invitation and positive peer `TrAns`, call `authorize_pair(side0, side1)`. Then route service-directed A0 `TrEnt` and A2 messages to `handle(peer, payload)`. The return is `None` for an unrelated family or a list of `Delivery(peer, payload, reload_profile=False)`; an empty list is an accepted operation requiring no immediate reply. Malformed or unsupported trade input raises `TradeError`. The adapter should abort the affected exchange and resynchronize, not fabricate a success reply. `begin(side0, side1)` is a trusted adapter/test entry that bypasses TrEnt consent checks; never expose it as a client-controlled operation.

`status(peer)` returns bounded diagnostic state. `abort(peer)` rolls back both sides. `disconnect(peer)` also removes that peer's binding and returns notifications only for its still-connected counterpart. Call the trade disconnect path before dropping the connection from routing. A disconnect after an already committed exchange does not undo it.

The adapter owns timeouts, delivery and durable storage. Persist `economy.snapshot()` **after mutation and before sending final A2/1E Quit replies**, using one atomic replacement for the whole account snapshot. There is no network or filesystem access in this module. If persistence fails, do not send successful completion and continue as if it succeeded; close/resynchronize and resolve the process/disk state before accepting further mutations. A stronger production transaction journal is outside this local service.

`Delivery.reload_profile=True` is actionable: close/resynchronize that connection and make it reload its committed profile before further play. A2 Dead or Quit cannot by itself undo the cards already reconciled locally after M4. All rollback notifications use this flag, including cancellation, so staged deck/discard changes cannot leave an apparently current client attached to older committed data.

The parent adapter plans raw POL service ID `0x400`; its TM game-domain identity is `0x400 XOR 0xB62DC8D0E4BCBB90`. Send every A1/A2 response from that service's scrambled IRC nickname. Do not send A2 responses from either trading player's nickname. The service module does not hard-code transport identity.

## Peer handshake and PTL advertisement

The adapter relays 21 `Tr`, 22 `TrAns`, 23 `TrCan`, 25 `Tr` rejection and 2A `TrCanAck` between authenticated peers. These are peer controls, not shop or table messages. Track the specific pair and acceptance; naming somebody in TrEnt is not consent. Simultaneous invitations resolve side0 to the lower unsigned game identity in the original client (`100895CD`); ordinary accepted invitations make the requester side0 and recipient side1. Both roles are explicitly mapped in `TRADE_CONTROL_SEQUENCE.md`.

Only side0 submits service entry:

1. A0/00 `@TrID=/ID=<16-digit service game identity>` goes **to the peer**.
2. A0/00 `@TrEnt=/NN=self|peer/HID=self|peer/Dm=self|peer/Vol=self|peer` goes **to that service**. NN uses 16-digit hex; the other pairs are decimal.
3. The service replies to **both** participants with A1/00 `@TrEntAns=/Ans=1` and then A2/18 Init. The recipient consumes the earlier TrID itself. Initial A1/Init parsers permit sender replacement, but the implementation must keep one stable sender.

Side assignment is copied from `102B5DF0` at `10015814`; only role0 calls TrEnt builder `1008A5B0` at `10015833`. Both roles poll A1 at `10015910`. The source peer of TrID must remain the actual inviter when relayed. The entry handler validates the first NN against the authenticated initiator and the second against the authorized recipient; HID/Dm/Vol are width-checked metadata, not authority to select another account.

Advertise the service in a raw PTL table record: 0x68 zero-filled bytes, little-endian game identity at +0, nonempty channel at +0x18, metadata beginning `D` at +0x28. The proposed record uses channel `#LOCALTRADE1` and metadata `D` followed by 48 `A` bytes, then NUL padding. PTL table count at +0x48 must include it; table array begins +0x5850. The trade controller scans **raw** exported records and chooses the last `D` entry (`100156E5..10015722`). `D` is excluded from the separate A/B playable-table projection; it need not pretend to be a playable table. A new original-code test runs the actual PTL copier, exporter, metadata decoder, derived-table producer and trade discovery on exactly this record.

The pre-entry queue has a verified double-dequeue bug. Wait for the real role0 TrEnt before emitting A1/Init. Do not pre-send service replies while invitation polling is still removing 21/22/25 messages.

## Session state and completion

Init is `A2/18 @Init=/ID=session@No0=/RA=/M=/CP=/S=@No1=...`. It sends both roles' committed metadata, preserves balances, and creates a nonzero session byte. `/M` in this Init does not set native visible money. Profile state must already agree. Every subsequent request must match the authenticated peer, role `/P`, and session byte.

- A2/1A offers contain `/P`, `/E=total`, `/S=start`, `/C=count`, and local `@N0..` reduced-six card tuples. Total is at most30; each request page at most15. The actual empty sender emits only `@Card=/P=side/E=0`. Pages must be contiguous and each cumulative prefix must match owned card multiplicity. Complete offers are relayed to the other side. Responses use up to15 records but shrink a page to fit the447-byte body limit; a maximum-width 30-card test emits14/14/2. This avoids the original finalizer's truncation behavior.
- A2/1B `Data` has `/P`, `/M=0|1|2`, and exactly30 selection bits in `/D`. A player's bits select cards in the **other** player's offer. Only0/1 are accepted, and all positions beyond that offer must be0. A changed offer clears the opposite selection and readiness. Changing a selection under a peer's M2 requires returning to an unconfirmed state first.
- Both current states2 cause A2/1C `@Data=/M=3` to both clients. Each original client answers `@Ans=/P=side/M=2` only when its own and peer states are2 (`10106BBE/10106BCB`). Both distinct valid answers are required before sending M4. Stale answers after cancellation of a challenge are ignored. A duplicate answer never replays destructive M4 reconciliation.
- M4 causes the native client to return unselected offered cards, remove selected offered cards, and accept selected peer cards. Received cards have deck slot255. Own unselected cards retain their association. The service mirrors that working state but does **not** yet change either committed profile.
- A2/10 Erase and A2/22 Decks operate on the staged owned collection. Erase Mode1 removes listed owned copies without currency credit; Mode2 is a no-list completion marker and never erase-all. Offered cards cannot be discarded from the pre-completion main inventory. After M4, incoming or existing cards may be discarded for overflow. Deck names1/2 accept up to17 non-NUL bytes.
- A2/1D Get marks that side complete only after its working collection fits1000 cards. **Both Get messages atomically replace both committed inventories**, then produce A2/1E `@Quit=/P=side/B=0`. Money is unchanged. Repeated Get before commit is a no-op; after commit it replays only that side's Quit receipt.

Temporary collection capacity is1030; persisted capacity is1000. Reduced tuples have no unique copy ID, so equal copies are consumed in stable stored order. A full edit batch is validated before applying any edit. The service never accepts client-chosen sale values, prices, points, or currency changes through A2.

Get is not a universal deck-editor barrier: a renamed editor can be detached and sent later. Decks is accepted while waiting for the other Get and also against the most recently completed session while that account remains idle. Late edits revalidate owned cards and can only change associations/names. Later Erase or confirmation messages are rejected after commit. Beginning another trade or disconnecting invalidates that receipt. The public economy method rejects delayed deck edits if another reservation or shop session has begun.

## Public economy boundary

Added public methods are `account_view`, `is_reserved`, `reserve_exchange`, `commit_exchange`, `release_exchange`, and `apply_deck_changes`. The reservation returns immutable account views and an identity-based capability. A second reservation or active shop prevents entry. Shop handling rejects reserved accounts; `validate_selection` returns false while reserved. The adapter should also reject table admission or a new trade if either connection is already in a live match; the economy service cannot infer transport/match state.

Commit checks both original snapshots, both output capacities, every card and deck slot, both sets of deck names, and combined card conservation except explicit removals. It cannot create cards or change their stats. It constructs both new states before assigning either, then releases both reservations, with no callback or await in between. The trade coordinator proves which side's selections authorize the particular transfer and which Erase tuples authorize removals. Currency is fixed to the reserved views. Rollback and process restart leave the last committed snapshot unchanged.

## Validation and limits

The tests cover consent/identity, exact Init and zero-offer schemas, pagination and maximum body widths, owned duplicate copies, changed confirmation, repeated Ans/Get, both-account commit, overflow discards, deck/name edits including delayed rename, rollback after the first Get, reserved-account protection, snapshot/profile roundtrip, and the recorded original paired-flow final ownership.

The optional Unicorn test executes **two original x86 clients** through server-produced Init, Card, Data, M3, M4 and Quit. It obtains outgoing Card/Data/Ans/Get from the original sender, executes full original reconciliation and fitting-card move, and compares every final card field and both balances with the Python service. Native compaction may reorder equal-rank cards, so the final comparison is a multiset. The original instruction fixtures substitute heap/runtime/presentation/final enqueue as documented in the existing probe modules. No installed game, desktop control, socket server or native two-player UI is launched.

No historical database semantics, UI timeout durations, or successful live two-player interoperability are claimed. Adapter routing, consent lifecycle, forced reload on rollback, and durable snapshot replacement remain required for a complete running service.
