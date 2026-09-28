"""One authenticated visible room per connection, across ordinary/event rooms."""

from tmserver.protocol.identities import GAME_KEY, service_nick


def validate_room_entry(server, peer, room):
    owner = server.table_for_peer(peer)
    if owner is not None and owner.room.room_root is not room.room_root:
        raise ValueError("leave the current table before changing rooms")
    event_room = server.event_matches.coordinator.participant_room(peer)
    watching = server.event_matches.spectators.admissions.get(peer)
    if watching is not None:
        binding = server.event_rooms.room_for_service(watching[0])
        event_room = binding.room_id
    if event_room is not None and event_room != room.room_id:
        raise ValueError("leave the current event selection/table before changing rooms")


async def leave_other_rooms(server, peer, key, room):
    # Called only after destination DE succeeded. Detach every old roster
    # before awaiting delivery, so a full/invalid destination never moves us.
    updates = []
    chat_updates = []
    for previous in server.ordinary_room_routes.values():
        if previous.room.room_root is not room.room_root:
            source = service_nick(previous.room.room_id ^ GAME_KEY)
            was_member = peer in previous.room.members
            updates.append((source, previous.room.leave(peer)))
            if was_member:
                chat_updates.extend(server.chat.leave(peer, source))
    for binding in server.event_rooms.rooms:
        if binding.room is not room.room_root:
            source = service_nick(binding.raw_owner)
            was_member = peer in binding.room.members
            updates.append((source, binding.room.leave(peer)))
            if was_member:
                chat_updates.extend(server.chat.leave(peer, source))
            server.events.leave_room(key, binding.event_id)
    for source, deliveries in updates:
        if deliveries:
            await server.room_updates(deliveries, source=source)
    await server.chat_updates(chat_updates)
