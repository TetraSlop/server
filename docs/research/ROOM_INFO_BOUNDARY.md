# Room player information string boundary

DE/PD field 10 uses **32 bytes of storage including its NUL terminator**, so the
maximum safely serialized and decoded text is **31 non-NUL bytes**. This is the
information region at player record `+38..57`, separate from the canonical name
field covered in [room name provenance](ROOM_NAME_PROVENANCE.md).

The original code establishes a string-storage contract, not a historical server
rejection code or a complete grammar for the information bytes.

## Original sender and receiver

The three original presence wrappers (`10087CE0` for DE, `10087B00` and
`10087BF0` for PD) copy exactly 32 caller bytes into shared record `102B3200+38`.
The serializer `101A3340` then scans that region for NUL at
`101A34DB..101A34E5` and appends the resulting string through `101A3504`.
The scan has no 32-byte boundary.

The probe puts either zero or `TAIL\0` immediately after the shared record.
All three original wrappers produce the same boundary behavior:

| Information text length | Following record bytes | Transmitted field 10 |
|---:|---|---|
|31|zero or `TAIL\0`|Exactly the 31 information bytes|
|32|zero|Exactly 32 information bytes|
|32|`TAIL\0`|The 32 information bytes followed by `TAIL`|

The latter is an executed source serialization overread, not a claim about what
normally occupies those adjacent globals. All canonical names are the short,
terminated `LocalPlayer`; this result does not depend on the separate name issue.

Incoming decoder `101A2880` extracts field 10 at `101A296A..101A2973` and invokes
`1019F480` with destination `record+38`, capacity 32, at `101A297C..101A2983`.
That helper tests for NUL before each copied byte. After 32 nonzero bytes it
reports **-8404** at `1019F4A3..1019F4C2`, without checking a 33rd source byte or
adding a terminator. Thus even exactly 32 bytes followed by NUL trigger the
diagnostic. A longer field retains only its first 32 bytes and has the same
diagnostic.

Original nested DE and PD executions with lengths 0, 30 and 31 complete without
diagnostics. Lengths 32 and 33 trigger -8404 and retain an unterminated information
region. In these fixtures the enclosing DD still advances its sequence and
updates the player; the diagnostic is not an admission rejection.

## PTL and derived room-player behavior

PTL copier `101A0C90` copies the raw `0x58`-byte player record and does not validate
the information string. A full-width negative control therefore enters through
PTL without the DE/PD diagnostic.

The inspected derived player projection (`10086010` then `10066950`) reads fixed
information offsets, rather than scanning the full region as a string. Its
observed reads end at information offset 27. The display name is copied using
the length byte at offset 15, constrained to 1..15, and explicitly terminated.
Status/substatus come from offsets 23/24. For the seeded ordinary-player template,
30-, 31- and 32-byte inputs give the same `LocalPlayer`, status 3 and substatus 1
through PTL and DD.

This bounds the finding: there is a proven sender overread and incoming string
diagnostic, but no reproduced visible crash or readiness corruption in this
projection. The probe does not establish that every other native consumer ignores
the final information byte. Retain opaque bytes within the terminated field.

## Production reproduction and compatibility scope

At discovery, `room_service.player_record` accepted 32 information bytes and
re-published them unchanged. Actual production DE and PD echoes reproduced the
original -8404 diagnostic. `_opponent_payload` also padded its synthesized CPU
information to 32 nonzero bytes, so ordinary Computer presence reproduced it
without malformed external traffic. The known original human information
template is 31 bytes.

A compatible output needs no invented metadata format: keep the existing fields
and reserve the final byte for termination. Limiting incoming information text
to 31 bytes and generating at most 31 CPU information bytes address the observed
boundary. Whether malformed requests close/resynchronize follows the existing
adapter policy; the source does not prove a new denial message. Silently
discarding arbitrary client data is not established by this evidence.

## Reproduction

```powershell
python -B research/probe_room_info_boundary.py --research-dir '<external tetramaster directory>'
```

The directory must contain the mapped image and existing `protocol_map` Unicorn
harness. **41 cases** verify 18 original sender/canary combinations, 10 original
DE/PD receiver boundaries, four PTL projections, eight production DE/PD operations
and one production CPU publication. Add `--require-safe-production` to require
that production no longer publishes the unsafe full-width field. Negative
original controls remain reproducible after a server-side fix.

At discovery the probe reported three unsafe production cases: full-width DE,
full-width PD and synthesized CPU presence. Output is
`results/room-info-boundary-original.json`; the invocation transcript is
`results/room-info-boundary-original.log`.

Mapped TM code SHA256:
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
Heap, locks, timestamp, diagnostic sinks, final transport and presentation sorting
are explicit harness boundaries. Original serialization, decoding, copying and
derived projection execute. No native application, desktop, network, original
asset or live save is used.
