# Fixed room rules through the original editor and Apply

Fixed rooms preserve their advertised game rules even when personal game
preferences differ. On initial human admission, the original client copies the
selected **PTL table rules** into its current settings, then skips the game-rule
editor for an RL type `A` room. It opens the table-options editor instead. Apply
therefore combines the fixed game preset with the player's saved table options.

The server's rejection of conflicting `14 Apply` game fields is compatible with
this path. No additional production change was required. This depends on the
server publishing matching rules in both the room directory and table metadata;
the RL display alone does not populate the outgoing game fields.

## Original instruction chain

1. RL projection `1008E08E..1008E46E` maps room type `A` to projected `+0x18C=0`
   and type `B` to `1`. Selection `1003074A..10030761` copies this into current
   room global `102B54EC`. Table projection `10066D10` retains that context at
   derived-table `+0x1C`; type `A` keeps the PTL game rules at `+0x20`.
2. The listed admission consumer `1008F95E` reads the actual queued
   `41000200@GameEA=/EN=1`. At `1008F9E5..1008FA33` it copies the selected table's
   rules into `102B61E0`, table options into `102B61EC`, and context into
   `102B61D8`.
3. Admission completion `10090114..10090143` chooses the next editor from that
   context. Zero posts event `03000E7A`; nonzero posts `03000E79`.
4. Original lobby dispatch `1004CA54` and jump table `1004DC20` route `E7A`
   directly to table-options constructor `100516F0(context, mode=0)`. `E79`
   instead constructs game-rule editor `1004EE20(mode=0)`. After its local
   commit, `10051356..10051374` posts `E7A` to continue into table options.
5. The initial game editor, when present, obtains personal defaults from
   `102B5ED4` through `1004ECA0`; initial table options come from `102B5EE0`
   through `100515C0`. These are distinct from the current game settings.
6. Table Apply `100533A0..10053496` commits table options, then calls original
   sender `10087410` with current game settings `102B61E0` and current table
   options `102B61EC`, both Save flags zero. It emits primary `14`.

The fixture executes all these data and branch steps. It captures actual editor
constructor arguments, including `context=0, mode=0` for fixed rooms and the
game-editor-then-table-editor sequence for the custom Open Tables room.

The later configuration action at `1004D66F` also checks current context
`102B61D8`: zero goes directly to the table-options constructor at `1004D6EB`;
nonzero constructs a game editor at `1004D78A`. This observation is from static
instructions; the captured fixture covers initial admission and Apply.

## Rule13 is a separate current-settings source

The independent control case queues an original `13` response with game fields
that differ from the saved personal defaults. Original receiver `10086E90`
parses it into the table's rules cache, `100873A0` exports that cache to
`102B5F04`/`102B5F10`, and game initializer `1004ECA0` in mode `1` reads those
current fields. The saved defaults at `102B5ED4` remain unchanged.

This proves the distinction between personal defaults (mode `0`) and current
queried rules (mode `1`). Initial fixed-room Apply does not need a Rule13 round
trip to obtain its game preset: the admission copy already obtained it from PTL.

## Captured requests and regression scope

[`tests/fixtures/fixed_room_apply.json`](../../tests/fixtures/fixed_room_apply.json)
contains four chains using actual generated files from the current example
configuration: Open Tables, Classic Rules, Special Rules and Quick Play. Their
saved game preferences are deliberately different from all three fixed presets:

```text
bm=0, du=0, st=0, cb=1, ca=0, gs=2, tl=5
```

Saved table options are comment `8`, card-level bounds `0..99999`, no password
and ordinary admission. Original Apply supplies rank bounds `au=300, al=100`.
For example, Special Rules produces:

```text
GTM0G14000000@Tet=/bm=0/du=0/st=1/cb=1/ca=1/gs=1/tl=2@Tab=/in=0/lu=99999/ll=0/au=300/al=100/co=8/pa=0
```

`original_payload` records exactly the original sender's message at the captured
send boundary; `apply` adds its normal `GTM0G` routing prefix. The builder also
passes each resulting request through that room's real `MatchService` validator.
The parent integration regression in `tests/test_room_rule_transitions.py`
replays the captured requests through encrypted sockets.

This is isolated original-instruction evidence, not a rendered native session.
Widget base construction and event posting are explicit boundaries. Editor data
initializers run unchanged; numeric text fields receive their initialized values
without user edits. The generic send is captured. All profiles, accounts and
rooms exist only in memory; no live save, native application or network is used
by the builder.

## Continue Conf1 boundary

Original alternate Continue sends `Conf=1` at `1010D375`. Its state `5` at
`1010D5D9` waits for gameplay `43/04 ComList`, then advances to state `9`.
This is the separately implemented computer-menu reconfiguration route, rather
than ordinary room events `E79`/`E7A`. No new fixed-human-room Apply implication
was established by this bounded check. Every result-button visibility condition
was not re-emulated; this work does not broaden ordinary-versus Conf1 support.

## Reproduction

```text
python -B research/build_fixed_room_apply_fixture.py --research-dir <tetramaster-research-directory>
```

The external directory must contain the existing `protocol_map` harnesses and
reconstructed TM code image `TM_emulated_mapped.dll`, SHA-256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
The fixture also pins the generated directory and individual PTL hashes. Python
requires Unicorn and the existing harness dependencies.
