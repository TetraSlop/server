# Rooms and Computer play

`Play.cmd` loads the default Local Room plus
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
Restart the launcher after updating to receive the corrected room permissions.
The other supplied rooms use human opponents; run `Play.cmd --clients 2` or
`--clients 3` to test them locally.

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

Unknown fields, booleans and out-of-range values fail configuration validation.
Nonzero wagers or double-up require the existing economic hooks; the supplied
presets keep both zero. An explicitly configured board must agree with the
room's special flags. For enabled double-up, an authorized Continue can increase
the current stake; the directory describes the starting stake.

The same preset supplies room advertisements, table advertisements and new match
state, including after reset. Rule queries return it. Conflicting Apply or
Computer configuration requests are rejected. Saving personal default settings
is separate and does not change a fixed room. Table-level settings such as
password and observation remain editable. Fixed-rule enforcement is explicit
replacement-server policy; the original client's display alone does not prove
the historical server's enforcement behavior.

The original initial-admission path also distinguishes these room types. Fixed
rooms copy game rules from the selected table's PTL metadata and open only the
table-options editor. Open Tables opens the game-rule editor with saved personal
defaults first. Both can use personal table options. The
[original editor trace](research/FIXED_ROOM_EDITOR.md) follows admission,
editor selection, initialization and the actual Apply sender. Its four captured
Apply messages pass encrypted replay against the corresponding supplied rooms.
Changing between rooms and restarting preserves saved personal game defaults.

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

[Original-client evidence](research/ROOM_COMPUTER_GATE.md) documents the room
flag at RL record `+0x29`, table prefix, card threshold and table-state guards.
The frozen fixture includes all five actual launcher rooms passing through
original directory parsing, room selection and action branches. Encrypted tests
also cover directory discovery, CPU entry, fixed human settings and game start.
These checks do not render the native menus; that remains on the
[native checklist](NATIVE_TEST_CHECKLIST.md).
