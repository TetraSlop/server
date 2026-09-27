# Opt-in spectator preview

Current extensions: [observation settings](../protocol_map/OBSERVER_RULES.md)
and [original Computer-menu opponents](../protocol_map/NPC_OBSERVERS.md).
WatchInfo preserves Observe and actual NPC selectors; event advertisements
agree with the configured preview option. The evidence limits below still apply.

The spectator adapter is disabled by default. It builds an original-client watch snapshot and copies public match events into bounded per-observer queues. It never registers a player, reserves an account, reads a participant's private hand into a packet, advances the match, draws random values, or participates in acknowledgement barriers. A frozen `MatchService.observer_snapshot()` is its only match-state input.

This is a paced preview, not a validated native spectator session. The original client has no recovered presentation-complete acknowledgement for role4. Its end-turn cleanup can erase a future PutCard/BattleData even when transport preserved order. A user-dismissed watch dialog also prevents proving any finite bootstrap delay. Keep the feature opt-in until native presentation testing establishes a suitable policy or a stronger original synchronization contract is found.

## Adapter API and integration

`SpectatorService(enabled=False, clock=monotonic, bootstrap_delay=8, turn_delay=3, action_delay=3, packet_delay=.1, max_backlog=256, max_observers=16)` accepts an injected monotonic clock. Delays are local policy measured in seconds, not recovered historical values.

- `handle(peer, payload, snapshot, epoch=match)` accepts exactly `43/25 @Data=/Watch=0`. It returns ordered `Delivery` objects, an empty list for a retry/deferred bootstrap, or `None` when not handled/disabled. An existing participant cannot watch. An observer cannot submit another primary43 gameplay action.
- `publish(participant_deliveries, snapshot, epoch=match)` copies public semantic events and may return due deliveries. Copies of the same event to different participants are collapsed; a repeated identical packet to the same participant remains a separate event, preserving tied battles.
- `poll(snapshot, epoch=match)` returns at most one due live delivery per observer. No subsequent live packet is released while a prior delivery remains outstanding.
- `record_sent(peer, payload, epoch=match)` must run after successful socket drain. It validates completion order and sets the next deadline from the actual completion time. The entire ordered bootstrap may be sent together; its final completion starts the bootstrap delay. A delayed writer cannot trigger catch-up bursts.
- `delay_after(payload)` exposes the preview delay policy, but an adapter using `record_sent` should not add another sleep.
- `disconnect(peer)`, `reset(new_match)`, `active(peer)`, `count`, and `take_evicted()` support cleanup. The last returns `(peer, reason)` pairs. Old-epoch publish, poll, and completion callbacks cannot overwrite the current epoch.

Call state methods under the existing match lock. Never await socket I/O while holding that lock. Use one bounded sender task per observer; preserve order, finish the write/drain, then reacquire the lock for `record_sent`. On write failure, disconnect only that observer. The adapter authorizes current table-channel membership and service identity before calling `handle`. It must also close/clean up evicted observers and discard queued writes from retired epochs.

Ordinary ExitCh/PART cleanup retires the sender as well as its observer state.
Every completion, failure and final cleanup checks that the sender still owns
the current admission. A delayed old WatchInfo cannot acknowledge a replacement
bootstrap even when both watches use the same match and identical payloads; an
old network error cannot close the new admission. The writer handles transport
errors within this ownership check, while ordinary participant delivery keeps
its existing failure recovery. Tracked gameplay and untracked PLAYACK replies
retain their existing ordering, and retired tasks stay tracked until shutdown
or completion. Encrypted re-entry fixtures verify both exit paths, unchanged
participant/account state, and current-writer failure isolation.

An observer requesting a snapshot during battle waits for the next clean selection/turn boundary. Unsupported snapshots, turn gaps, backlog overflow, replaced rounds, and replaced matches remove only the observer. Ordinary two-seat, sixteen-cell matches are supported; three-player boards and post-result/replay spectator flows are outside this preview.

## Original watch entry before 43/25

The watch path does **not** need 41/GameEN admission or a player reservation:

1. Role4 constructor enters watch sub09. At `100BB3B8` it constructs the direct-table child initialized by `10092AE0`.
2. Child state1 resolves the table's advertised uint64 identity in the live 0x68-byte records. `10092BC9` calls `100860F0`, which sends primary2D `@CheckJoinTable` and starts the ordinary table channel transition using the record's identity/channel.
3. Child state2 polls channel success at `10092C2E`. It then sends primary01 `@Req` at `10092C84`.
4. Child state3 polls primary02 `@PLAYACK` through `100869E0` at `10092D1F`. EN must equal1. State4 posts local event `03001F5A` at `10092D97`.
5. Parent watch handler receives that event at `100BB3E5`, drains gameplay queues, and sends `43/25 @Data=/Watch=0` at `100BB422`.

Therefore an active-table nonparticipant may receive current PLAYACK settings/roster after joining, without being registered as a match player. Retrying Req should repeat that read-only reply.

The normal successful state0..4 path does not consume the 2E CheckJoin reply. Its parser `10086570` packs `Join | (Mem << 1)`; a separate recovery branch `10093090` selects state200 for nonzero and202 for zero. The original meaning of these two integers is not established. Assigning Join1/Mem0 to a watchable local table is an explicit local policy, not a recovered historical admission rule. Evidence is also in `../protocol_map/spectator_entry_evidence.asm`.

## Snapshot contract and corrected CH meaning

Selection-phase bootstrap is only `43/25` header0 WatchInfo. Turn-phase bootstrap is ordered `43/25` header1 WatchInfo, `43/26` WatchCard, then the exact last emitted TurnData09 including its original shared random bytes.

WatchInfo sends two bounded hex-encoded names, the shifted rule vector `st|cb|ca|tl|du|gs|0`, Q0/C0/L0 for both seats, and G0|0|2. The synthetic computer remains an ordinary versus seat; no native NPC selector is invented. Header1 also sends remaining hand counts, sequence, active seat, seat mapping0, and16 F/CH values. WatchCard contains only the played board cards as sparse `@N{boardIndex}=/D={reduced6}` sections. Ten played cards fit one native-size body.

F's upper two bits contain owner+1 for ordinary cards. Its low six bits reconstruct the tile/property: ordinary property0/1/2/3 maps to0/3/4/5; special models8001..8008 map to1..8, spinner8009 to9+direction, and800A/800B to17/18. Pending combat markers and unrepresentable properties are rejected.

**CH is the signed battle modifier byte, not compact-card points.** WatchInfo places it temporarily in compact board slot+4 (`10102077`). Reconstruction saves that byte at `100C1CEB`, recalculates ordinary card points from catalogue/stat values at `100C1CF2..100C1D42`, imports the card at `100C1D76`, and restores CH into rendered+26 at `100C1D8B..100C1D8F`. The serializer sends the byte as unsigned0..255; the render/combat consumer later interprets it as signed. Original property survives at rendered+24. Snapshot reconstruction sets all three owner bytes to current owner; it does not retain original owner history.

The module relays public StartData08, TurnData09, PutCard0A, BattleData0C, ResultData0D, GetAway23, Change24, Dead27 and DataError28. It does not publish VsGameInit, selection packets, private TCList, or account writes. Output ends at the first ResultData; later Ready/Continue, rewards and next-round behavior are not presented as implemented spectator flows.

## Headless validation and limitations

`python -B -m unittest test_spectator_service -v` runs21 test methods. Five execute original x86 instructions across37 seeded cases: watch child ordering and parent Watch request; complete ten-card WatchInfo/WatchCard/TurnData parsing;16 property/modifier reconstruction combinations;18 special F constructors; and the end-turn future-PutCard loss. Native fixtures substitute heap, transport, locks, diagnostics and graphics dependencies; the entry-order test substitutes channel completion and the Req wrapper, while executing original CheckJoin and Watch builders and PLAYACK consumer.

Other tests cover default-disabled behavior, immutable match/RNG state, participant barrier independence, bounds, duplicate requests/events, battle-deferred snapshots, epochs, observer-only eviction, privacy, actual-send deadlines, blocked writers and retired completions. Tests run entirely headlessly. No native game launch, GUI input, server run, or native spectator timing validation was performed.

Known limits remain: arbitrary user-dialog duration; original future-packet queue cleanup; no recovered observer progress signal; only two-seat stable snapshots; no historical timing policy; no full post-result observer lifecycle. The implementation deliberately exposes these limits instead of claiming reliable unattended spectator playback.
