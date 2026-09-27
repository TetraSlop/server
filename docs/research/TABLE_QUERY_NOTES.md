# Rule and CheckJoinTable queries

The recovered native paths do not establish a need to query another table's
rules while already seated or watching. `Rule` is used to reload the current
admitted table's settings; `CheckJoinTable` belongs to channel entry and recovery.
This differs from the already recovered cross-table `GameM` roster inspection.

## Current-table rules

Lobby event `03000C8B` reaches `1004D04B`. If current admitted table identity
`102B61D0/102B61D4` is zero, it creates no query. Otherwise it passes that identity
to child constructor `1006BAD0`. The identity is populated after successful
`GameEA` at `1008F9E5..1008F9F3`; the query does not take an arbitrary highlighted
table identity. Child `1006BBB5` calls lookup/sender `10086D80/10086DD0` and emits:

```text
GTM0G12000000@Rule=
```

The request contains no account name, membership flag or rule parameters. Its
destination identifies the table. Reply primary13 contains the current `@Tet`
and `@Tab` fields, as used by `MatchService.rules_body()`. Original `10086E90`
matches the sender against the table directory, parses the settings into that
table's cache, and `100873A0` exports the cache to `102B5F04/102B5F10`. Subsequent
editor mode1 reads those current fields; saved personal defaults are separate.
See [FIXED_ROOM_EDITOR.md](FIXED_ROOM_EDITOR.md).

The probe executes the actual lobby event, constructor arguments, sender,
response parser and cache export. It confirms no request without an admitted
table, and the current identity as the destination with one. This does not prove
every possible UI visibility state or authorize changing query ownership policy.

## CheckJoinTable and its recovery result

Sender `100864E0` takes the target identity and emits the empty request:

```text
GTM0G2D000000@CheckJoinTable=
```

Entry wrapper `100860F0` sends it at `1008611B` **before** beginning the channel
transition at `10086143`. Its callers include spectator entry `10092BC9` and
Computer entry `100958A6`; the latter follows `GameECA`, whereas spectator entry
precedes spectator `Req`. The ordinary successful spectator path does not wait
for the CheckJoin reply. Recovery state210 sends another query at `10093041`;
state211 consumes it at `1009309B`.

The reply shape is:

```text
GTM0G2E000000@CheckJoinTable_Ans=/Join=1/Mem=0
```

Parser `10086570` computes `Join | (Mem << 1)`. Its recovered caller at
`10093090` tests only whether the combined result is zero:

| Join | Mem | Packed | Recovery action |
| --- | --- | --- | --- |
| 0 | 0 | 0 | State202: skip the channel-cleanup child. |
| 0 | 1 | 2 | State200: create the channel-cleanup child. |
| 1 | 0 | 1 | State200: create the channel-cleanup child. |
| 1 | 1 | 3 | State200: create the channel-cleanup child. |

The state200 child has vtable `10205FCC`, update method `100922B0`. Its first step
calls `10086430` and emits `GTM0G20000000@ExitCh=` to the current transition's
table. Subsequent steps return to the room channel. Thus the established visible
effect is whether recovery performs table-channel cleanup. **No recovered
consumer distinguishes the Join and Mem bits individually.** Their historical
server-side meaning is still unproved; their names alone do not establish seat
availability, participant membership or permission to observe.

The replacement server currently gives registered participants `Join=1,Mem=1`.
For nonparticipants it sets Mem from available participant capacity and Join from
that capacity or an available active spectator preview. Those mappings are
explicit local policy. The original instruction evidence neither proves them
historically accurate nor demonstrates a concrete failure requiring a change.
The current restriction against querying another table while already attached
has no demonstrated incompatibility in the recovered Rule/CheckJoin paths.

## Reproduction

```text
python -B research/probe_table_queries.py --research-dir <external-research-directory>
```

The external directory must contain `protocol_map/table_admission_vectors.py`
and the established reconstructed `TM_emulated_mapped.dll` code image, SHA256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
The script prints its results and writes no fixtures. It checks both Rule event
branches, the Rule response/cache export, the CheckJoin sender and all four
Boolean reply combinations including the first cleanup step. Local output is
retained in `results/table-query-original.log`.

All player/table/scene/message state is synthetic. Generic widget constructors,
dialog reset, text measurement and runtime/transport boundaries are substituted.
The recovery cleanup child's first step is seeded after identifying its original
vtable; the later channel transition is not executed. No native game, rendering,
physical input, listener or live save is used.
