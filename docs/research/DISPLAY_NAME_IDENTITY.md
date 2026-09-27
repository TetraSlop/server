# Account names and bounded table/event presentation

A canonical account name may occupy the full 16-byte saved field. Several
table and event destinations instead need a NUL within 16 bytes. Projecting the
first 15 printable bytes for these messages preserves their original string
contract while leaving canonical account names and numeric identities intact.
This does not establish safe support for 16 data bytes in those wire fields.

This projection covers table/event labels. A later [room-name source probe](ROOM_NAME_PROVENANCE.md)
found a separate native DE/PD limitation: a full 16-byte name lacks a terminator
and can run into adjacent metadata during serialization. Synthetic server-wire
support for that length does not establish native room sender/display safety.

Distinct full account names can share the same displayed prefix. The traced
receivers continue distinguishing them by character ID or nickname identity;
they do not use that prefix to select the local account, seat or event progress.

## Original consumers

| Route | Name destination | Identity selection |
| --- | --- | --- |
| PLAYACK / GameML | MLNN and HN are separate terminated 16-byte fields in each 0x38-byte cached member record | MLID is a separate qword at record+0 |
| VsGameInit | CN is copied into 16-byte slots starting at `102B6218`; `1010264E..10102656` copies through NUL without a bound | `/ID` is compared to saved character ID `10294BD4` at `1010250E..1010251D`; seat rotation follows at `10102546..10102568` |
| GameOwner | CN/QN are decoded by `10084CF0` and copied through NUL into caller buffers at `10084DD4` / `10084E0D` | NN is returned as a qword; controller `100A9294..100A92A5` compares it to local `102B5308/0C` |
| EventMemberList | Record+8 contains 16 name bytes; `100782CD..100782D8` copies exactly 16 into the participant view | Record+0 is a qword; projection copies it at `100782DD..100782F0`; sorter/local summary `1006C5C0` selects by the local identity |

GameOwner is an ownership notification, not a replacement for the owner stored
in PTL/TD metadata. These probes do not alter that earlier established rule.

The event file has an 8-byte header and up to 256 records of 0x28 bytes. The
participant-view record is a different, 0x70-byte structure. Its name is at+10
and steps at+5C. A name projection changes neither the file's qword identity nor
the steps used to identify and rank the local participant.

## Reproduction

```powershell
python -B research/probe_display_name_identity.py --research-dir '<scratchpad>/tetramaster'
```

The probe requires the external `protocol_map` Unicorn harness and reconstructed
`TM_emulated_mapped.dll` code image, SHA-256
`422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.
It uses current production table/event serializers, not reconstructed message
builders. Synthetic canonical names `ABCDEFGHIJKLMNO1`, `ABCDEFGHIJKLMNO2` and
`ABCDEFGHIJKLMNO3` deliberately collapse to the same 15-byte presentation.

**34 original-consumer cases pass** against the production projection: four
PLAYACK cases and ten each for VsGameInit, GameOwner and EventMemberList.
They cover two and three players, fresh accounts and restored snapshots:

- PLAYACK retains each distinct MLID and terminates both name fields.
- VsGameInit finds each recipient's character ID and rotates the correct seat
  even though every CN is identical.
- GameOwner's original decoder terminates CN without touching the following
  sentinel bytes; its controller flags only the matching NN as local owner.
- Actual EventHost membership files preserve distinct qwords. Original view
  projection and sorting recover each recipient's own steps and rank.
- Canonical account names and complete saved profile bytes stay unchanged.

The fixture supplies a table owner and the post-settings notification state; it
does not replay the complete admission editor flow. Event progress is synthetic
trusted domain input. Original receive, string-copy, identity and projection
code execute with the existing harness's runtime/transport substitutions. No
native application, desktop control, external connection, original asset write
or live save is involved.

Captured output is retained under ignored
`results/display-name-identity-original.json`. It includes exact generated
packets, event-file hashes, numeric identities and native seat/progress results.
The canonical 16-byte inputs are passed directly to production registration
and event admission; the probe does not shorten their output itself. No identity
mismatch remained in these paths, including the deliberate display collisions.
The separate portable regression suite owns server admission and persistence
validation; this research specifically checks original consumer compatibility.
