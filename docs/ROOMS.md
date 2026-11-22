# Rooms and Computer play

`tmserver --rooms examples/auto_rooms.example.json` adds the rooms below to the
default Local Room; see
[`examples/auto_rooms.example.json`](../examples/auto_rooms.example.json).
This is a local room lineup, not a claim about the historical service's lineup.

| Room | Players | Rules |
| --- | --- | --- |
| Local Room | One human and one or two CPU opponents | Choose Computer settings. |
| Open Tables | Two or three humans | The owner chooses settings. |
| Classic Rules | Two or three humans | One-minute turns, no special board flags. |
| Special Rules | Two or three humans | One-minute turns, Special Tile, Chance Block and Rotating Block enabled. |
| Quick Play | Two or three humans | Thirty-second turns, no special board flags. |

For CPU play, enter **Local Room**, select an empty table and choose **Computer**.
The native menu requires at least five cards; zero money does not disable it.
Restart the server after updating to receive the corrected room permissions.
The other supplied rooms use human opponents; connect two or three loader windows
to test them locally.

## Configuring rules

The standalone server's `--rooms` option accepts the existing room configuration
format. An optional `rules` object fixes that room's game rules and applies to
every table in it. Omit `rules` to let the table owner configure them. For example:

```json
{
  "rooms": [
    {
      "name": "Special Rules",
      "room_raw": 259,
      "table_raw": 515,
      "room_channel": "#SPECIAL",
      "table_channel": "#SPECIALTABLE",
      "mode": "versus",
      "players": "auto",
      "rules": {"st": 1, "cb": 1, "ca": 1, "tl": 2}
    }
  ]
}
```

`players: "auto"` chooses two or three human seats when the owner starts.
`mode: "computer"` permits one human to choose one or two NPCs through Computer.
Additional `tables` inherit the parent room's rules and retain their own mode.

| Field | Meaning | Accepted values | Default |
| --- | --- | --- | --- |
| `bm` | Starting wager | Integer 0–2000 | 0 |
| `du` | Double-up | 0 or 1 | 0 |
| `st` | Special Tile | 0 or 1 | 0 |
| `cb` | Chance Block | 0 or 1 | 0 |
| `ca` | Rotating Block | 0 or 1 | 0 |
| `gs` | Departure rule | 0 Substitute, 1 Invalid, 2 Loss | 1 |
| `tl` | Turn time | 0: 15 seconds; 1: 30 seconds; 2: 1 minute; 3: 3 minutes; 4: 5 minutes; 5: 10 minutes | 2 |

If a fixed room contains any Computer table, its rules must explicitly include
`"gs": 0`. The original hosted Computer constructor sets that value to zero;
its initialization message and rule editor cannot change it. Other values are
rejected during configuration loading instead of creating a room whose native
start request will fail. Human-only rooms support all three departure rules.

Unknown fields, booleans and out-of-range values fail configuration validation.
Nonzero wagers or double-up require the existing economic hooks; the supplied
presets keep both zero. An explicitly configured board must agree with the
room's special flags. For enabled double-up, an authorized Continue can increase
the current stake; the directory describes the starting stake.

The same preset supplies room advertisements, table advertisements and new match
state, including after reset. Rule queries return it. Conflicting Apply or
Computer configuration requests are rejected. Saving personal default settings
is separate and does not change a fixed room. Table-level settings such as
password and observation remain editable. Enforcing fixed rules is a server
choice; the client only displays them, and the original server's behavior is
unknown.

The original initial-admission path also distinguishes these room types. Fixed
rooms copy game rules from the selected table's PTL metadata and open only the
table-options editor. Open Tables opens the game-rule editor with saved personal
defaults first. Both can use personal table options. Four
captured Apply messages from the client pass encrypted replay against the
corresponding supplied rooms. The messages are described in
[TetraPackets irc/G/14.md](https://github.com/TetraSlop/packets/blob/main/irc/G/14.md).
Changing between rooms and restarting preserves saved personal game defaults.

Computer uses a separate rule editor that remains editable even in a fixed
room. Its edits must still agree with the preset; opening it also clears the
client's double-up option. Direct opponent selection preserves the received
double-up value, so the server does not prohibit that preset. Prefer custom
rules for CPU rooms when players should freely change these options. The
supplied lineup keeps Local Room custom and all fixed rooms human-only.
The client's Computer menu is described in
[TetraPackets client/computer-player.md](https://github.com/TetraSlop/packets/blob/main/client/computer-player.md). How the
client shows custom fixed CPU rooms has not been checked.

## Computer availability

The native room directory has separate flags for fixed-rule display and Computer
permission. Custom rooms display rules as "Not Set"; fixed rooms display their
preset. Neither display choice alone enables Computer.

The server advertises Computer permission only in rooms containing a CPU table,
and advertises each table's capability separately. Table metadata prefix `A`
allows Computer, while `B` preserves human Join and disables Computer. This also
supports rooms containing separate human and CPU tables. A direct Computer
admission to a human table receives a refusal without claiming the account or
disconnecting it.

[TetraPackets irc/L.md](https://github.com/TetraSlop/packets/blob/main/irc/L.md) documents the room
flag at RL record `+0x29`, table prefix, card threshold and table-state guards.
The frozen fixture includes all five actual launcher rooms passing through
original directory parsing, room selection and action branches. Encrypted tests
also cover directory discovery, CPU entry, fixed human settings and game start.
These checks do not render the native menus; that remains on the
[native checklist](NATIVE_TEST_CHECKLIST.md).

## Room and channel identity

JOIN, PART and channel messages accept ASCII capitalization differences while
keeping each configured channel's advertised spelling. Repeating a join with
different capitalization still uses one membership. Punctuation remains distinct;
this is local compatibility behavior, not a recovered historical IRC case policy.

Room presence uses the authenticated character name, separately from a handle
alias. Entry and updates with another name are rejected before publication.
Full 16-byte names have an existing native sender/receiver limitation despite
server-wire support; see [TetraPackets irc/L.md](https://github.com/TetraSlop/packets/blob/main/irc/L.md).

## Table entry limits

The server checks a new participant's saved inventory and money before claiming
a seat. Human and Computer entry require at least five owned cards. Joining an
occupied, configured human table also requires enough money for its wager and
collection points within both advertised bounds, inclusive. The native menu
calls these bounds "Card Level", but the original check uses collection points,
not rating or the displayed level tier.

Creating an empty custom table skips the bounds and wager check because its
owner chooses the rules after entry. An empty fixed human table still requires
the preset wager. Computer entry has its separate rule menu, so it checks only
card count. Existing members can retry admission after settings change, and
spectators need no owned cards.

An ineligible request receives the existing nonfatal unavailable/start-failure
reply without reserving the account or changing the table. The same connection
can retry when eligible. This response is a server choice: the client's menu checks are known
([TetraPackets flows/tables.md](https://github.com/TetraSlop/packets/blob/main/flows/tables.md)), but not the original
server's error codes for these conditions.
