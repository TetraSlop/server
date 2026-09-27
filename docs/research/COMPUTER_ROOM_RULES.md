# Computer menu rules and fixed room compatibility

The hosted Computer menu always starts with Quit Rule `gs=0`. Its initialization
message cannot communicate another value. A fixed CPU-capable room that requires
`gs=1` or `gs=2` therefore rejects an unchanged native start. This is a concrete
client/server incompatibility, not a proposed historical server policy.

`tests/fixtures/computer_room_rules.json` freezes six original-instruction chains,
including actual `MatchService` initialization messages and final client packets.
The player, room, account preferences and UI objects are synthetic memory. No
native application, network listener or live save is used.

## Quit Rule initialization

1. The known hosted Computer constructor path reaches `100B9D1B` only for
   Computer mode `BL=0` and alternate mode `AL=0`. `100B9DA1` explicitly writes
   that zero to player global `102B64C9`, or `P+E9`. A previous `gs=2` value is
   cleared, even when selected-room/current-table context indicates fixed rules.
2. Original receiver `10101A10`, subtype2, consumes the production
   `ComGameInit`/`ComList`. `10102D21..10102ED1` writes seven rule values:
   `bm|st|cb|ca|tl|du|view`. `view` is `P+D9`; **it is not `gs`**.
   There is no eighth initialization field read into `P+E9`.
3. The original final UI send at `100EBBD0..100EBBEE` invokes `10106E30` with
   subtype6 and the selected player count. `101071E8` appends `P+E9` as the eighth
   value. Seeding the known opponent selection to ID1 yields this exact packet:

   ```text
   GTM0G43000602@ComGame=/Rule=0|1|0|1|4|0|0|0/Com=1
   ```

   This matches fixed `bm=0,du=0,st=1,cb=0,ca=1,gs=0,tl=4`. The same packet
   conflicts with otherwise identical presets having `gs=1` or `gs=2`.

The isolated replay confirms both rejection cases leave the menu and RNG state
unchanged. An unedited `gs=0` start succeeds. Both saved `Tet.gs=0` and
`Tet.gs=2` produce the same compatible packet: saved ordinary-table preferences
do not override these Computer initialization boundaries.

Public room configuration should reject nonzero `gs` when any advertised table
supports Computer. Human-only fixed rooms can retain all three values. The
generic match protocol can keep decoding the complete `gs` domain for other
proven message paths; this finding does not justify changing the wire format or
silently ignoring a configured preset.

## Fixed context does not lock the Computer editor

The Computer menu uses its own editor, separate from the ordinary human-table
editor described in [FIXED_ROOM_EDITOR.md](FIXED_ROOM_EDITOR.md).

- Parent event `0400002F` routes through `100EB99D..100EBACF` and enters state5.
  Its `100EBCAF..100EBD52` path constructs the Computer rules editor at
  `100ED480`. The fixture executes this event and construction path with both
  fixed and custom room context.
- The editor initializes selectors from the received player fields at
  `100ED50E..100ED58C`. It reads `st`, `cb`, `ca`, `tl` and `view`; there is no
  Quit Rule selector or saved ordinary `Tet` copy in this initialization.
- Its Apply branch `100EE8E5..100EE95B` writes those five choices and sends
  subtype6/header0 with seven rule values. It does not write `gs`. Changing the
  native time-limit selector produces a conflicting save and subsequent start
  in a fixed room; the current strict server rejects both. Custom rooms accept
  the same operation.

This proves a limitation of fixed CPU presets: the original Computer controls
remain editable even though server policy requires the preset. It does not
establish historical server enforcement or simulate every visible/input state.
Adding an undocumented reply or silently replacing the client's choices is not
supported by this evidence.

Opening this optional editor also sets `du=0` at `100ED4F6`. It is **not** part
of the initial Computer admission or opponent confirmation. The separate
`fixed_du1_optional_editor` vector successfully starts an unchanged `du=1`
configuration; only opening the editor clears it. This does not justify banning
all fixed CPU configurations with double-up enabled. It does mean such a preset
can also conflict after the player opens settings, even without changing its
other selectors.

## Reproduction and boundaries

```text
python -B research/build_computer_rule_fixture.py --research-dir <external-research-directory>
```

The script uses the established external `protocol_map` Unicorn harness and
reconstructed `TM_emulated_mapped.dll`, code-image SHA256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
This hash identifies the executable instruction image, not the decoded PlPrm
opponent asset. Captured output is retained locally in
`results/computer-room-rules-original.log`.

The constructor slice ends before unrelated collection/grade initialization
`10101530`, which does not write `gs`. The complete original queued
ComGameInit/ComList parser and final serializer run unchanged. Resource prefetch,
generic widget base constructors, widget selection and final transport are
substituted; rendering and physical input are excluded. Initial NPC selection is
seeded to known valid ID1. Direct in-memory `MatchService` instances intentionally
permit hypothetical incompatible presets so the evidence remains reproducible
after public room-configuration validation rejects them.
