# Configured event rooms

EventRooms connects explicit EventHost configurations to original directory and
room consumers. No events are enabled by default. E/F announcements are exactly
EventConfig.service_records(): E remains absent until its match implementation
is enabled; F may advertise the separately implemented prize collection route.

## Adapter API

Construct `EventRooms(event_host)` once after EventHost restore. `.rooms` is a
tuple of frozen EventRoom bindings with `event_id`, `room`, `raw_owner`,
`zone_index`, `room_id`, and `room_channel`.

- `room_for_service(game_identity)` returns the binding for its room, E, or F
  identity. These are game-domain IDs, not raw profile owners or IRC nicknames.
- `room_for_channel(channel_bytes)` finds its room/E/F route case-insensitively.
  Use EventHost.event_for_service to distinguish the route kind; matching an
  E/F binding alone is not authorization for ordinary room admission.
- `directory_files()` returns ZL and all generated RL files, including the
  unchanged default Local Lobby/Local Room. Live event presence counts are
  reflected on each call.
- `profile_file(raw_owner,path)` returns the directory/EventList files or the
  exact room-owned PTL/EventDataList/EventMemberList. Pass FileOwner.raw_identity.
  Unknown/incorrect owners yield None for room-specific files. The root
  provider must not turn that into a default-room or single-event fallback.
- `disconnect(peer,account_key)` removes transient presence/admission and
  returns `[(binding, [(recipient, DD/PC), ...]), ...]`. It performs no network
  I/O or persistence, and retains durable membership/progress.

Each event RoomService has no ordinary playable A/B table and no default D
trade advertisement. Its PTL contains only enabled configured E/F records,
and its room directory advertises zero ordinary tables with sixteen members.
F's channel length remains the existing fifteen-byte maximum. Room channels
are deterministically `#EV` plus eight base36 digits of the raw41-bit identity
(eleven bytes), stable across config reorder and collision-free.

For normal services, RoomService's original defaults are unchanged:
`RoomService(player_count=2, *, room_id=ROOM_ID, room_channel=ROOM_CHANNEL,
table_id=TABLE_ID, table_channel=TABLE_CHANNEL, service_records=None)`.
Explicit service_records replaces the default D record; table_id=None suppresses
the normal table and makes set_table invalid. Constructor checks reject route
collisions, invalid channel extents and more than128 live table/service rows.

## Required room-entry ordering

The native event scene begins the room-owned EventDataList read, sends D1/04,
and sends DE before consuming all their completions. File reads and D1/04 must
therefore work before DE. After validating DE against the authenticated nickname,
character, and selected room identity, use that binding's RoomService.handle.
Only a successful DS-bearing admission should trigger EventHost.admit_room.
Persist durable changes before publishing D0; send DS/DD in room order and then
D0, with that room's service nickname as source.

If EventHost returns a negative D0, the adapter should clean up transient room
presence on the client's subsequent exit/disconnect and prevent service
admission. This module does not reinterpret the native negative-answer branches.
Moving between rooms should remove previous transient room presence and call
EventHost.disconnect before admitting the new event; durable membership remains.
Ordinary table/game traffic must continue using the ordinary table route.

The adapter must also reject configured service identities that overlap its
account identities. EventRooms rejects overlap with existing fixed service
IDs and channels; it does not own the server's authenticated account registry.

## Exact key association and bounds

Original zone projection 1008D429..1008D43F copies ZL record+0x11 into its
56-byte UI record's name. The selected-zone copy 1002CA3C..1002CA53 writes that
record to102B5324. Original room projection1008E0B9..1008E0E8 copies RL+0x2A
through the asterisk terminator into room-view+0x13C.

EventList join100396F2..1003984B passes 102B5324 and room-view+0x13C to
1008B670. The lookup compares both case-sensitive strings. On success it
copies the listing scalar/flags/times/text into the room view and inserts a
corresponding event view. There is no separate event identifier encoded in an
unused RL gap. The served zone name is exactly key_region; room name is exactly
key_event. The two tested events share the same room name in different regions
and select their own distinct identities.

Event RL records use prefix B, whose proven effect is the Not Set rule panel.
The exact-name EventList join establishes event association; B is not asserted
to be an event-type enum.

Reachable directory limits are stricter than EventList's own32-byte fields:
zone keys must be1..26 bytes plus NUL; room keys1..30 bytes plus asterisk before
the packed-rule overlap. Room keys containing NUL or asterisk are rejected.
The default Local Lobby/Local Room pair is reserved. At most32 zones including
the default are emitted; EventHost already enforces32 listings. A listing scalar
of FFFFFFFF is rejected because it is the original unmatched sentinel. Other
scalar/flag meanings stay opaque. IDs must reverse to the original41-bit raw
POL domain; unsupported arbitrary qwords are rejected, not truncated.

## Validation

`test_event_rooms.py` has8 passing tests covering exact keys, stable routing,
default parity, live counts, strict owner isolation, authenticated room identity,
DS-before-D0 integration boundary, transient disconnect, and rejected configs.
`event_room_vectors.py/json` has6 original-code groups: two complete zone
projections/selected copies, two complete RL+EventList joins, and two actual
PTL-to-F-selector paths. Each includes generated server data, not separately
reconstructed expected files.

No native game, desktop input, or GUI was launched. Full socket dispatch and
durability ordering belong to the root adapter's encrypted integration tests.
