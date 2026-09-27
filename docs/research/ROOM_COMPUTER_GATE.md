# Room directory and Computer availability

The original client requires **ASCII `A` at RL room-record offset `+0x29`**
before it enables Computer on an empty ordinary table. The previous directory
builders populated `+0x28` but left `+0x29` zero. That explains an enabled human
Join action beside a grey Computer action without any `GameENC` reaching the
server. A backend configured for computer play does not supply this client flag
implicitly.

These are three separate fields in the native `0xC8`-byte room record:

| Field | Original consumer | Meaning established by the client |
| --- | --- | --- |
| `+0x28` | `1008E1D9..1008E452` | `A` decodes advertised room rules; `B` presents rules as “Not Set”. |
| `+0x29` | `1008E1C8`, `1008E1E0`, `10045D78` | Must be ASCII `A` for the room to allow the Computer button. Zero and `B` both disable it. |
| `+0x70` | `1008E0FF..1008E148`, `100310B4..10031105` | Chat metadata. A nonzero decoded value draws “Chat” / “Off”; this is not the Computer switch. |

For `+0x70`, the renderer uses original `Lobby.BIN` strings 213 (“Chat”) and
375 (“Off”). Keep this field unchanged when enabling Computer.

## Complete selection path

The original RL projection at `1008E08E..1008E46E` reads `+0x29` into the
projected room's DWORD `+0x19C`. The room-selection branch at
`1003074A..10030761` copies the entire `0x1D8`-byte projected record from
`1024EC68` to `102B5360`. Its `+0x19C` therefore becomes global `102B54FC`.
The Computer constructor compares this global to `0x41` at `10045D78`.
Setting `+0x28` to `A` cannot satisfy that comparison.

There are preceding table-state guards. The constructor's original table lookup
and mode dispatch at `10045ABC..10045BBE` select the Computer-eligible branch
only for raw table state zero. Within that branch, `10045D56..10045DC9`
requires all of:

1. Table metadata prefix `A`, decoded at derived-table DWORD `+0x8A8`.
2. Room flag `+0x29` equal to ASCII `A`, via global `102B54FC`.
3. At least five cards, counted by `100B8B30(collection, 0x406)`.

The help codes are 22 when available, 23 for insufficient cards, and 25 for a
disallowed room/table. Their original text includes “Play against the computer.”,
“You need five cards or more to play against the computer.”, and “You cannot play
against the computer in this room.”

The per-frame update at `100475D3..1004768A` can additionally disable all table
actions when the derived action flags' low nibble is 2. For example, raw state
zero with a nonzero player count triggers that guard and replaces the Computer
hint with code 19. An ordinary empty advertisement needs state/count/owner zero.
Waiting or playing tables also construct Computer disabled.

## Money and human-only tables

**Zero money does not disable Computer.** The fixture executes the original
constructor with both zero and 1,000 money, 25 cards and an empty table; both are
enabled once `+0x29` is `A`. It also advertises a 500 wager with zero money:
Computer remains enabled while the original human Join branch reports its
insufficient-money hint (20). The latter compares the table wager against
global `102B64A8` at `10047819..10047833`.

Table metadata prefix `B` is accepted by the original PTL parser and ordinary
table projection. Comparing otherwise identical `A` and `B` records in empty
and one-player waiting states changes exactly two derived bytes: the retained
raw prefix at `+0x870` and decoded type at `+0x8A8`. Rules, ownership, count,
state and action flags match. The original human Join branch remains enabled
in both states, while the Computer constructor rejects the `B` table. This
provides a native advertisement for a human-only table in a mixed room.

These flags describe client presentation. The server must still authorize the
requested table mode and enforce its configured rules. They do not establish
the historical hosted service's room lineup or policy.

## Reproducible evidence

[`tests/fixtures/room_computer_gate.json`](../../tests/fixtures/room_computer_gate.json)
contains 12 original Computer cases and two table-prefix parity cases. They cover
the missing byte, its correction, room rule type `B`, table prefix `B`, card
threshold, money/wager independence, inconsistent empty-table counts, waiting
tables and active tables.

It also passes the **complete five-room directory returned by the current
`Server.profile_file`** through the original projection, retaining the native
room indices, and parses each actual `RoomService.snapshot()` PTL. This uses
three in-memory synthetic accounts and `examples/auto_rooms.example.json`.
Local Room enables Computer at zero money; every room permits human Join;
Classic, Special and Quick project exactly their configured fixed rules. The
fixture retains the actual directory hash, room-record bytes and PTL hashes.

Run the following with Python containing Unicorn and the established external
research harness:

```text
python -B research/build_room_computer_fixture.py --research-dir <tetramaster-research-directory>
```

The directory must contain `TM_emulated_mapped.dll` and `protocol_map` with the
existing room, directory and admission harnesses. The reconstructed TM code
image has SHA-256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
The script executes original RL/PTL parsing, projections, room selection copy,
table-mode dispatch and relevant action branches. It supplies the inventory
count at the existing collection-call boundary and captures widget enable calls.
It does not create widgets, render a scene, run the native game, use a network,
or read a live save. The separately exercised human Join branch is an isolated
consumer check; it does not bypass the full menu's earlier invalid-table guard.
