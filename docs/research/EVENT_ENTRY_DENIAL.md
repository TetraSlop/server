# Original event-room denial and room cleanup

The original client automatically clears its room presence after receiving
`EventEn/Ans=-32870`. It sends the room `PC` message before showing the specific
rejection notice. A temporary physical room-roster entry between successful
`DE` and that cleanup is therefore compatible with this client flow. It need
not become an admitted event participant or a persistent event member.

This extends the previously mapped entry-state result with the actual parent
scene, exit child, outgoing `PC` builder and rejection-notice branch. It does
not recover historical admission policy from the shared denial code.

## Original control flow

All addresses refer to the reconstructed, memory-layout TM code image with
SHA-256 `422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879`.

1. The existing entry machine sends the room `DE` through `10087CE0` and waits
   for its result through `10087DE0`. A positive result advances entry state 5
   to state 6. Only then does `10096C6D` call `1008CDF0` to consume `D0/EventEn`.
   There is no separate outgoing `D0` admission request.
2. `10096C94` recognizes exactly `-32870` and selects child state 210. On its
   next update, `10096DE3..10096DF0` posts parent event `03001F75` and advances
   to 211. Other negative answers select 200, then post `03001F70` instead.
3. Parent branch `1006F840..1006F98A` maps `03001F75` to parent state 13.
   `1007022E..10070258` constructs the exit wrapper with reason 5. Its original
   constructor block `10070CCB..10070D3F` creates the cleanup child using
   `100993D0(mode=0)`. The original cleanup constructor initializes state 0.
4. Cleanup state 0 calls `10085F40(0)` / `101A1180` (`sqMgCpExitRoom`). The
   successful path advances to state 1; this is not a user confirmation.
5. Cleanup state 1, `10099572..100995CB`, reads the **selected room identity**
   from `102B54E0/4` and calls `10087E60`. This executes
   `101A13D0` (`sqMgCpSendPlayerClear2Rm`) and the original text builder
   `101A3620`, using the authenticated nickname identity from `102B2918/1C`.
   The synthetic probe captured this exact body at the final transport boundary:

   ```text
   target = 31425364758697A8
   body = <PC>\x070x2132435465768798\x07
   ```

   Here `\x07` denotes a BEL byte, not four literal characters. The surrounding
   room transport prefix is outside the intercepted text-builder body.
6. Cleanup state 2 calls `10085FC0(0)` / `101A12A0`
   (`sqMgCpExitRoomCheck`). Success advances to state 3 and clears
   `102B535C`. After the progress display completes,
   `10099CD6..10099D00` posts `03001040` to the exit wrapper.
7. The reason-5 wrapper branch `10071BD1..10071C2E` waits for completion events
   `03001040/41`, then opens the rejection notice using text-table offset
   `23D0`. The probe executes this branch after the captured cleanup event.
   Its later acknowledgement branch is separate from sending `PC`.

The cleanup mode matters: mode 1 skips the room-clear sender at
`1009960C..10099621`. The denial path above demonstrably constructs mode 0.

## Executed cases and limits

Eight isolated original-instruction cases pass:

| Input/control | Observed result |
| --- | --- |
| `EventEn/Ans=1`, `2`, `2147483647` | Entry state 7, no denial cleanup |
| `EventEn/Ans=0` | Remains waiting in state 6 with the wait helper returning 0 |
| `EventEn/Ans=-32870` | Full denial-to-`PC`-to-notice chain above |
| `EventEn/Ans=-1`, `-2147483648` | Generic failure event `03001F70`, parent state 12 |
| Cleanup mode 1 | State 1 advances to 2 without sending `PC` |

UI base construction, event delivery, dialog output, and transport exit status
are controlled boundaries. The receiver, state branches, cleanup constructor,
room-clear sender and message formatting execute original instructions. The
probe neither launches the original application nor renders a dialog. The
transport-success case establishes the normal ordering, not a wall-clock
cleanup deadline or a guarantee after packet loss. Exit failures have the
existing retry/error branches; no new historical timeout is inferred.

The original client distinguishes physical room presence from the event's
member file. The already mapped `TM0EventMemberList` has a fixed `0x2808`-byte
layout (8-byte header, up to 256 records of `0x28` bytes). That format limit does
not prove retail capacity policy, nor does one `-32870` distinguish expiration,
exclusion, capacity or another denial reason. Current server choices remain
documented local policy in [EVENT_HOST_NOTES.md](EVENT_HOST_NOTES.md).

No restoration of the previously occupied room occurs in the traced successful
cleanup path. The evidence does not require rolling back a completed room
transition when event admission is denied. The server should still clean
physical presence on the resulting `PC` or actual disconnection, and should
not grant event admission, reservations or rewards from `DE` alone.

## Reproduction

Use the external read-only research directory containing
`TM_emulated_mapped.dll` and `protocol_map/event_contract_vectors.py`:

```powershell
python -B research/probe_event_entry_denial.py --research-dir '<external tetramaster directory>' > results/event-entry-denial-original.json
```

The saved output is `results/event-entry-denial-original.json` (ignored research
output). The probe verifies the code-image hash and asserts all eight cases.
No runtime implementation or portable test expectation is changed by this note.
