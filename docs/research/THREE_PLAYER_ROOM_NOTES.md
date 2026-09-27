# Three-player room advertisement

The room service now accepts `RoomService(player_count=2)` with supported values
2 and 3. The default remains two. The root server should construct it with the
same configured count as MatchService and continue publishing the actual
occupancy and owner through `set_table`. Admission limits and start barriers
remain MatchService responsibilities. RoomService rejects occupancy above its
configured limit before changing metadata, history, or sequence.

## Original client evidence

Ordinary three-player versus uses the existing **A** table metadata and table
states. The 49-byte metadata count at positions 4..5 is the current occupancy,
not a maximum-seat setting. The original table exporter/decoder accepts A and
B; C is skipped. No recovered field establishes B as a three-player selector,
so the implementation does not invent that meaning.

The actual RoomService snapshot and DD/TD packets were passed through original
101A0C90, 101A2D50, 101A07E0, 10086010 and 10066D10. Their derived records then
fed the original Join-menu update branch at 100477BC..100479A6.

| Raw state | Occupancy | Derived flags (other player owner) | Original Join button |
|---|---:|---:|---|
| 0 | 0 | 0x00 | Enabled, claim mode |
| 1 | 1 | 0x11 | Enabled, join mode |
| 1 | 2 | 0x25 | Enabled, join mode |
| 1 | 3 | 0x35 | Disabled |
| 2 | 3 | 0x33 | Disabled |

At 1004785B the occupied-table gate compares derived record+0x54 to literal 3.
Counts below three can join when the client has at least five cards, sufficient
wager balance, and a level within the table's bounds. States 2/3/4/5 are blocked
at 10047883..100478A4; state6 is blocked at 100477CB. These are client UI gates,
not a substitute for server admission checks.

The game initialization selects the player mode independently: VsGameInit
`/N=3` sets P+E5 and P+E6=N-2; the board-size lookup at 1023359E contains
16 and 25. See GAMEPLAY.md and the gameplay agent's three-player receiver
fixtures for the full three-seat initialization, rotation, and game sequence.
No new table prefix, raw state, or rule bit is needed to select this mode.

## Directory and policy boundaries

`directory_files()` needs no player-count parameter. RL+0x6A is the number of
room tables (one playable table), and RL+0x6D is the room member capacity
(sixteen). They are not per-table seat limits. The separate D-prefix service
record remains in PTL for trade; it is omitted from the client's playable-table
collection. RL type A/B controls fixed-rule presentation, not a proven seat
capacity.

The configured two-player cap is a local server policy. The original occupied
table menu alone permits joining occupancy2, so server admission must enforce
its configured cap even though the client may still offer Join.

A-prefix metadata in an A-type room also permits the empty-table Computer
button when the player has enough cards. Three-player computer games remain
unsupported; the adapter must reject the computer-admission route when
configured for three players rather than silently switch modes. The proven
ordinary three-player route is human-only.

## Validation

- `test_three_player_room.py`: 4 tests for capacity, atomic rejection,
  unchanged directory meaning, and original instruction consumers.
- Existing `test_room_service.py`: 7 tests, all passing.
- `protocol_map/three_player_room_vectors.py/json`: 8 fixtures, all passing:
  five real snapshot-to-decoder-to-Join-menu cases and three sequential real
  DD/TD updates.

These are headless original-code and Python tests. No game, GUI, or socket
session was launched for this change.
