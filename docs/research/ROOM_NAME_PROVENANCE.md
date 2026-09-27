# Room DE/PD name provenance

Room player field 4 is the character name (`CN`), not the separately supplied
handle name (`HN`). Authenticating it against the account's full canonical name
is consistent with the original sender. It should not be authenticated against
a 15-byte display projection, nor silently treated as a freely editable alias.

The original sender has a separate limitation for names filling all 16 bytes.
The evidence below preserves that limitation rather than making a claim of
complete native compatibility for every name accepted by the server's account
format.

## Sender and receiver evidence

Addresses refer to the reconstructed, memory-layout TM code image, SHA-256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.

The three room-presence builders are `10087CE0` (`DE`), `10087B00` (`PD`), and
`10087BF0` (another `PD` wrapper). Each prepares the shared `0x58`-byte player
record at `102B3200` using the same sources:

| Record field | Original source | Meaning established here |
| --- | --- | --- |
| `+00`, wire field 0 | `10294BD8/BDC` | Nickname identity |
| `+18`, wire field 1 | `10294BD4` | Character/game identity |
| `+28`, wire field 4, 16 bytes | `10294BE0..BEF` | Character name |
| `+38`, wire field 10, 32 bytes | Caller-supplied information pointer | Separate room/player metadata |

For example, the `DE` name copies are four DWORD loads/stores at
`10087D68..10087DAA`; the information block is copied separately at
`10087DB1`. `101A1C40` and `101A2180` then call the original serializer
`101A3340` for `DE` and `PD`, respectively.

The character-name label is verified independently of this layout:
`1008347C..10083496` appends the literal `/CN=` and encodes `10294BE0` in the
`GameEN` sender. Immediately afterward, `100834AF` onward appends `/HN=` from a
different argument. Original `GameEN`, `GameENC`, and `Req` constructor controls
all emit `CN=LocalPlayer` alongside `HN=Example`. The room-presence cases also
seed other profile/handle strings with different values, while observing only
the selected character name in field 4.

On receive, `101A2880` decodes the room player record. At
`101A28EB..101A2904`, it extracts field 4 and invokes `1019F480` with destination
`record+28` and capacity 16. Field 10 is independently copied into `record+38`
with capacity 32. The original nested room-update consumer applies both `DE`
and `PD` to its live player array. The probe checks the resulting nickname,
character identity, name bytes, metadata and sequence using the exact sender
body, without replacing its name bytes.

## The full-width name limitation

The outgoing serializer at `101A341B..101A3444` scans `record+28` for NUL. It
does not stop at the 16-byte name boundary. Consequently:

| Canonical name | Following metadata | Original transmitted field 4 |
| --- | --- | --- |
| `ABCDEFGHIJKLMNO` (15 bytes) | `Metadata` | `ABCDEFGHIJKLMNO` |
| `ABCDEFGHIJKLMNOP` (16 bytes) | Empty | `ABCDEFGHIJKLMNOP` |
| `ABCDEFGHIJKLMNOP` (16 bytes) | `Metadata` | `ABCDEFGHIJKLMNOPMetadata` |

All three presence builders reproduce this result. The source record itself
still contains exactly the first 16 name bytes and the separately copied
metadata; the overrun occurs while scanning that record for serialization.

The incoming helper `1019F480` copies at most the supplied capacity. If a NUL
is not found within it, `1019F4A3..1019F4C2` reports error `-8404` and returns
without adding a terminator. The executed room consumer still advances its
sequence and retains the 16 copied name bytes. The probe observes this
diagnostic for both the exactly-16-byte and concatenated forms. It does not
claim that later presentation of that unterminated field is safe.

This is a deferred native sender/presentation limitation. It does not justify
accepting arbitrary room aliases, authenticating only the first 15 bytes, or
stripping guessed metadata suffixes at the server boundary. Synthetic
16-byte server-wire tests establish server behavior; they are distinct from
evidence that a real client can reliably construct and display such names.

## Narrow channel comparison control

The table-cache lookup `101A2B20` compares its requested channel against each
PTL table record's `+18` string byte-for-byte at `101A2B3D..101A2B59`. With the
advertised `TABLE000001`, the original routine returns index 0 for that exact
spelling and `-1` for `table000001`. This supports preserving advertised spelling
in table-cache data. It establishes neither IRC transport case mapping nor
punctuation folding or JOIN/PART response-echo policy; those are separate paths.

## Reproduction and scope

```powershell
python -B research/probe_room_name_provenance.py --research-dir '<external tetramaster directory>' > results/room-name-provenance-original.json
```

The external directory must contain `TM_emulated_mapped.dll` and the established
`protocol_map` Unicorn harness. The probe verifies the code-image hash and
executes **23 cases**: three independent `CN`/`HN` constructors, 18 complete
room sender/receiver pairs (three wrappers, three name lengths, two metadata
choices), and two channel-cache comparisons.

Heap, locks, timestamp, host callback, diagnostics and final transport boundaries
are substituted. Original serializers, field decoder, bounded name copy and
live player-array update execute. Output is retained at
`results/room-name-provenance-original.json` (ignored). No native application,
network, original asset, live save or runtime implementation is changed.
