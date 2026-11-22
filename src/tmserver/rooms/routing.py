"""One authenticated visible room per connection, across ordinary/event rooms."""

from tmserver.protocol.identities import GAME_KEY, service_nick


def validate_room_entry(server, peer, room, *, allow_lobby_exit=False):
    previous = None
    owner = server.table_for_peer(peer)
    if owner is not None and owner.room.room_root is not room.room_root:
        if not allow_lobby_exit or not owner.can_leave_lobby(peer):
            raise ValueError("leave the current table before changing rooms")
        previous = owner
    event_room = server.event_matches.coordinator.participant_room(peer)
    watching = server.event_matches.spectators.admissions.get(peer)
    if watching is not None:
        binding = server.event_rooms.room_for_service(watching[0])
        event_room = binding.room_id
    if event_room is not None and event_room != room.room_id:
        raise ValueError("leave the current event selection/table before changing rooms")
    return previous


async def leave_other_rooms(server, peer, key, room):
    # Called only after destination DE succeeded. Detach every old roster
    # before awaiting delivery, so a full/invalid destination never moves us.
    others = [
        (service_nick(t.room.room_id ^ GAME_KEY), t.room.room_root, None)
        for t in server.ordinary_room_routes.values()
    ]
    others += [(service_nick(b.raw_owner), b.room, b.event_id) for b in server.event_rooms.rooms]
    updates = []
    chat_updates = []
    for source, previous, event_id in others:
        if previous is room.room_root:
            continue
        was_member = peer in previous.members
        updates.append((source, previous.leave(peer)))
        if was_member:
            chat_updates.extend(server.chat.leave(peer, source))
        if event_id is not None:
            server.events.leave_room(key, event_id)
    for source, deliveries in updates:
        if deliveries:
            await server.room_updates(deliveries, source=source)
    await server.chat_updates(chat_updates)
