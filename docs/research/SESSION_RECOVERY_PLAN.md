# CPU practice session recovery and turn watchdog

The implementable recovery policy is to release a disconnected or stalled CPU
practice table and require fresh admission. The client supplies ordinary moves
when its own choice timer expires; it has no demonstrated participant-resume
handshake. A server timer therefore provides a grace/watchdog boundary, not a
new timeout packet or a second source of human moves.

This pass added `match_watchdog.py`, `test_match_watchdog.py`,
`test_session_recovery.py`, and `test_match_watchdog_integration.py`. The main
agent owns the corresponding edits in `server.py` and `services.py`. All **26
new headless tests pass**: 16 policy tests, five session regressions, five
integrated deadline/cleanup tests. No listener, game process or UI was opened
by these tests. The writer replaces transport I/O; the real server, match,
economy, room, trade and reservation code still performs logical operations.

## Original-client constraints

| Evidence | Established behavior | Server implication |
|---|---|---|
| `MATCH_TIMEOUTS.md`, table `10233590`; checks `100C3FD4`, `100C5D4E`, `100CE9EA` | `tl=0..5` denotes 15,30,60,180,300,600 displayed seconds. Original clock advances by `60/scale` per updater; actual wall-clock frequency was not measured. | These are configuration durations; server monotonic grace is explicitly local policy. |
| `100C309D/100C320A`, `100C3295`, `100C43AF`, `100CE558` | Client resets its baseline while waiting for TurnData. Hand/placement/battle target choices then share one turn budget. | Do not spend decision budget on an undelivered/unacknowledged TurnData. Do not renew the decision budget on BattleSelect. |
| `100D86BE..100D86DE`, `100D3A60`, `100CEC69..100CECA4` | Local expiry submits normal CardSelect43/07, PutData43/0A, BattleSelect43/0B. Turn expiry uses strict `>`; pregame completion begins at equality. | There is no additional wire timeout opcode or automatic pass. Let those ordinary requests arrive before server grace expires. |
| `100C7386..100C73C9`, `100CF509..100CF53E`, `100CA964..100CA997` | All three responses to missing-reply dialog162 return to the same receive wait without resending. | The server cannot wait for a retry to repair its missing PutCard/BattleData/GetSelect response. |
| `DISCONNECT_RECOVERY.md`, full43 dispatcher audit | No recovered participant reconnect/resume request, replay cursor or repair snapshot. Observer WatchInfo/WatchCard is a different path. | Do not promise that a new socket resumes the old board. |
| `10104FF6..1010509D`, `101050A2..10105174`, worker `100C0AB0` | GetAway ignores a normal participant's own seat; duplicates increment departure count again. Change only installs a catalog computer in a departed eligible seat, and can be consumed too early as a no-op. | GetAway-to-self/Change is not CPU practice reconnect. Do not use replayed GetAway as an idempotent snapshot. |
| `100D90D6`, `100EEAC7`, `1010F7C1`; `100BADF4/100BC763` | Server Quit and Dead are polled in particular exit/init scenes, not universally during combat. | Do not invent an unsolicited Quit/Dead packet as a guaranteed global abort. Socket termination follows the client's established connection-error path, with native UX still unverified. |
| `SERVICE_CONNECTION_LIFECYCLE.md`, `100817A0`, `10080ED0`, `10081080` | Closing/reinitializing does not universally flush application queues or six learned service identities. | Fresh transport authentication is not fresh application admission. Reject/ignore stale gameplay before a supported new admission flow. |
| `RESULT_REWARD_SEQUENCE.md`, `100CB5F1..100CB854`, `10101530` | Selected cards leave the working collection; result reconciles them locally. There is no mandatory whole-profile upload or reread at Continue. | Preserve committed authoritative inventory/growth across disconnect; a future TM0DataFile must contain committed state. Do not roll back an accepted result because its delivery later fails. |

The cited original suites contain 189 timer, 66 departure, 143 connection and
146 result case groups. This pass reused those reports; it did not rerun or
claim new original-x86 vectors. The server tests exercise replacement code.

## Concrete bugs reproduced, then covered by fixes

Initial read-only probes used the following source snapshots:

```
server.py        507bb080c7ce919a7d91f17fb5dab381b74d38b4b8a8ed4c66fb025d14f12dec
services.py      83405e761bbbc9658dce19bf826f5c6851a296f73b9757066eb05bec66fb0b04
match_service.py 777a3bf120f73b7e658eab6f8ba368007eab9157f131d19c5049312aff415527
```

| Initial reproducible defect | Required boundary / current regression |
|---|---|
| A failed room recipient raised out of a healthy requester's command; `accept` would close the requester and release its table. | Per-recipient delivery isolates transport failure and continues to healthy recipients. Test drives actual table admission, room publication and GameEA. |
| `close` marked closed but kept nick/channels/session membership and writer open while notifications awaited another peer. | Detach synchronously before outbound awaits. A second Session can bind the same account while old notifications wait. Late old cleanup must not remove the replacement mapping. |
| An early trade-disconnect exception skipped room/table release; the transport vanished but the closed human remained in the match. | Independent cleanup stages. Injected failure still releases match reservation, table and transport. |
| A send that passed its first closed check could wait on the lock, then write after close. | Recheck closed state under the send lock. Test confirms no write occurs. |
| Rule-only table query registered a human seat; after reservation integration, the same path could reserve inventory. | Stateless Rule/CheckJoin discovery does not register/reserve. Stale ack/Break/Quit and unrelated payloads cannot claim a fresh table. |

The adapter currently accepts explicit GameEN/GameENC and its supported direct
Req route as admission. Permitting direct Req is the local adapter's existing
supported route, not evidence that every retail flow omitted GameEN. Normal
listed admission still uses GameEN → GameEA, settings/roster and Req → PLAYACK
→ GameOK. Discovery and arbitrary first table traffic must stay read-only.

`reset_match` now cancels pending combat, releases reservations and replaces the
match object. Publication and queued workers compare the captured match object
with the current one. This prevents a delayed old continuation from rewriting
new table metadata or delivering old gameplay to the replacement session.

## Implemented watchdog API and local policy

```python
watchdog = MatchWatchdog(clock=time.monotonic,
    ack_timeout=120, decision_grace=60,
    result_timeout=900, processing_timeout=30)
watchdog.observe(match, epoch=match)  # after handle/advance, before awaiting I/O
failure = watchdog.expired(match, epoch=match)
```

`observe` returns an immutable current wait description or None. `expired`
re-observes only its already-tracked match/generation, then returns the
description at/after the deadline. An old-generation query returns None and
does not change the current binding. The description fields are `reason`,
`epoch`, `phase`, `sequence`, `round_id`, `missing_peers`, `started`, `deadline`.
All times are injected monotonic seconds. Neither operation changes the match,
sends messages, acknowledges a client, or chooses a move.

| Wait | Default policy | Progress that changes the deadline |
|---|---|---|
| `card_selection` | Configured tl duration +60 seconds from selection observation | A new round. Partial selections do not renew the remaining client's budget. |
| `turn_ack` |120 seconds from new turn observation| A new turn. Partial/duplicate ack does not extend it. |
| `placement` / `battle_selection` |tl+60 from observing all human TurnData acknowledgements| A new turn. Placement, changed battle candidates and return from server processing preserve the same deadline. |
| `server_processing` |30 seconds for a pending battle target, or an unexpectedly idle acknowledged CPU turn| A different pending target/new turn. Repeated tied BattleData on the same target cannot keep it alive indefinitely. |
| `result_ready` |900 seconds from result observation| A new round; partial Ready does not extend remaining peers. |
| `result_continue` |900 seconds after every human is Ready| A new round; repeated Ready/Continue data does not renew it. |
| Lobby/configuration, finished, no human |No automatic timeout in this class| Explicit leave/disconnect and ordinary session heartbeat remain separate. |

During pending server processing the processing deadline takes precedence;
the human turn deadline remains stored. If processing later returns to a human
choice, that earlier decision deadline still applies. Default120/60/900/30
values are **chosen local policy**, not recovered historical limits. Ready is
an outgoing presentation barrier; this watchdog invents no Go acknowledgement.

One explicit implementation dependency is `MatchService._pending_battle`, used
only to identify the pending target. `pending_work`, phase, sequence,
completed_matches, players, selected cards, acknowledgements, ready and
continues provide the other observed state. If the internal target field is
renamed, expose it through a read-only property and change this one observer.

## Adapter integration and recovery ordering

`Server(watchdog=...)` now permits clock injection. `check_match_timeout()`
returns Timeout or None. It reads the current match under `match_lock`, rechecks
the exact epoch before acting, and closes sessions outside the lock. Observe
after each `handle`/`advance` and on replacement; also check periodically even
when socket PING/PONG traffic continues. The integration tests prove live PING
and duplicate TurnData ack do not postpone application expiry.

For the active CPU practice table, watchdog expiry/disconnect follows this
boundary:

1. Stop the old session from accepting/sending additional work; detach account,
   channel and transport membership synchronously.
2. Cancel old pending combat, release the match's inventory reservation and
   reset the table/NPC metadata. The old match object is no longer authoritative.
3. Preserve previously committed results. Completed result growth is committed
   idempotently and persisted before ResultData/TCList publication. Disconnect
   before that settlement discards transient match state, not durable cards.
4. Deliver resulting room/trade notifications independently. A failing observer
   cannot close a healthy requester or block logical account reuse.
5. Authenticate a new transport from scratch, then follow explicit bootstrap,
   profile load, room entry and supported table admission. Reuse durable account
   identity and service addresses as appropriate; never transfer old peer-object
   ack sets, selected-card indices or board state merely because the nick matches.

No self-GetAway, speculative Change, forced placement, automatic pass or
unsolicited Continue is emitted. Existing ordinary Continue leave still needs
header>1 and local Ans3, as shown in the original result fixtures.

## Validation and remaining limits

```
python -B -m unittest test_match_watchdog -v              #16 pass
python -B -m unittest test_session_recovery -v            #5 pass
python -B -m unittest test_match_watchdog_integration -v  #5 pass
```

The integrated tests expire selection/ack/decision, confirm reservation and
table release, re-admit the same account, and execute stale/cancelled worker
entry against a newer table without mutation. They use actual server logical
state with fake transport and no elapsed sleep.

Native connection-error presentation, retry UI and post-timeout relaunch remain
unverified. Fresh account admission works at the server boundary; transparent
in-place game recovery is not claimed. Historical penalties, forced-move policy
and server timing remain unavailable from the client. This CPU policy also does
not implement retaining a live human-versus-human match after one participant
disconnects; that needs a separate departure policy using the documented
GetAway/Change contracts. No timer is currently assigned to an idle table's
configuration UI. Adding one would be another explicit local policy.
