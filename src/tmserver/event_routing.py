"""Authenticated event room and F-card adapters; no event match success stub."""

from tmserver.claim_transactions import event_transaction
from tmserver.options_service import save_options
from tmserver.room_routing import leave_other_rooms, validate_room_entry
from tmserver.tm_protocol import parse


async def dispatch_event(session, target, payload):
    server = session.server
    event_id, route = server.event_routes[target]
    config = server.events.config(event_id)
    key = server.ensure_account(session.nick)
    view = server.economy.account_view(key)
    if (
        (
            route == "init"
            and config.match_enabled
            and config.entry_answer == 1
            and payload.startswith((b"GTM0G98", b"GTM0GB2"))
        )
        or route == "room"
        and payload.startswith(b"GTM0GD3")
    ):
        identity = config.init_id if route == "init" else config.room_id
        if await server.event_matches.dispatch(session, identity, payload):
            return True
    if payload.startswith(b"GTM0L") and route == "room":
        binding = server.event_rooms.room_for_service(config.room_id)
        commands = parse(payload[5:])
        entering = len(commands) == 1 and commands[0].token == b"DE"
        if entering:
            validate_room_entry(server, session, binding.room)
        was_member = session in binding.room.members
        responses = binding.room.handle(
            session,
            payload,
            authenticated_identity=(view.nn, view.player_id),
            authenticated_name=view.name,
        )
        if responses is None:
            return False
        chat_updates = (
            server.chat.leave(session, target)
            if was_member and session not in binding.room.members
            else []
        )
        if entering and session in binding.room.members:
            await leave_other_rooms(server, session, key, binding.room)
        with event_transaction(server, (key,)) as state:
            if entering and session in binding.room.members:
                responses.extend((session, p) for p in state.events.admit_room(key, event_id))
            if len(commands) == 1 and commands[0].token == b"PC":
                state.events.leave_room(key, event_id)
        await server.room_updates(responses, source=target)
        await server.chat_updates(chat_updates)
        return True
    if payload.startswith(b"GTM0G420001") and route == "room":
        replies = server.chat.enter(session, target, payload, identity=view.nn, name=view.name)
        await server.chat_updates(replies)
        return True
    with event_transaction(server, (key,)) as state:
        if payload.startswith(b"GTM0G84"):
            replies = [save_options(state.events.economy, key, payload)]
        elif route == "card" and payload[:7].upper() in (b"GTM0G90", b"GTM0GB2"):
            replies = state.cards.handle(key, payload, event_id=event_id)
        else:
            replies = state.events.handle(key, payload, event_id=event_id, route=route)
    if replies is None:
        return False
    for payload in replies:
        await server.deliver(
            session, b":" + target + b"!p@local NOTICE " + session.nick + b" :" + payload
        )
    return True
