"""Service metadata sent during client startup.

Server choice: the values describe this server's data, not the original
service's. Reply keys and pipe-separated elements come from TM 0x100812e0 and
0x100ab080.
"""

from tmserver.protocol.messages import service_notice

from contextlib import AsyncExitStack

from tmserver.economy.claim_transactions import auction_transaction, prize_transaction
from tmserver.economy.packets import parse_named
from tmserver.events.routing import dispatch_event
from tmserver.protocol.identities import SHOP_RAW, TRADE_RAW, game_id, service_nick
from tmserver.profiles.options import save_options
from tmserver.profiles.transactions import character_profile_request
from tmserver.rooms.routing import leave_other_rooms, validate_room_entry
from tmserver.protocol.commands import parse

# Directory 1, version 1 for every list; Shm names the shop service.
TEACH_DV_ANSWER = b"GTM0GE2000000@TeachDVAns=/D=1/V=1/AC=1|1/RK=1|1/Shm=%016X|1|1" % game_id(
    SHOP_RAW
)


async def reply(session, source, answers):
    for answer in answers:
        await session.send(service_notice(source, session.nick, answer))


async def dispatch(session, target, payload):
    # Reject unframeable service routes before any handler can commit state.
    service_notice(target, session.nick, b"")
    server = session.server
    key = server.ensure_account(session.nick)
    ordinary_table = server.ordinary_table_routes.get(target)
    ordinary_room = server.ordinary_room_routes.get(target)
    event_table = server.event_matches.routes.get(target)
    if payload[:7].upper() == b"GTM0G80":
        await reply(session, target, [server.commerce.initialize_profile(key, payload)])
        return True
    # Chat entry and option saves mean the same at every table and room service.
    chat_target = event_table is not None or ordinary_table is not None or ordinary_room is not None
    if chat_target and payload.startswith(b"GTM0G420001"):
        view = server.economy.account_view(key)
        await server.chat_updates(
            server.chat.enter(session, target, payload, identity=view.nn, name=view.name)
        )
        return True
    if (
        chat_target or target in (service_nick(SHOP_RAW), service_nick(TRADE_RAW))
    ) and payload.startswith(b"GTM0G84"):
        with server.commerce.edit_account(key) as economy:
            answer = save_options(economy, key, payload)
        await server.deliver(session, service_notice(target, session.nick, answer))
        return True
    if event_table is not None:
        return await server.event_matches.dispatch(session, event_table[1], payload)
    if target in server.event_routes and await dispatch_event(session, target, payload):
        return True
    if payload.startswith(b"GTM0A"):
        commands = parse(payload[5:])
        operation = commands[0].token if commands else None
        if operation in (b"ER", b"BR", b"SI", b"BH"):
            server.advance_auctions()
            if operation in (b"SI", b"BH"):
                # Search files are private connection snapshots; only expiry
                # advancement above can change the durable auction ledger.
                replies = server.auction.handle(key, payload)
            else:
                with auction_transaction(server, (key,)) as auction:
                    replies = auction.handle(key, payload)
            await reply(session, target, replies)
            return True
    if (
        ordinary_table is not None or ordinary_room is not None or target == service_nick(SHOP_RAW)
    ) and payload[:7].upper() in (b"GTM0GB0", b"GTM0GB2", b"GTM0G70"):
        prefix = payload[:7].upper()
        # B0 is shared with the shop. Parse the full envelope even when its
        # subtype belongs elsewhere, so malformed requests still fail closed.
        collection_entry = prefix == b"GTM0GB0" and parse_named(payload)[1] == 1
        replies = None
        collecting = server.auction.collection_active(key)
        if collection_entry or (prefix == b"GTM0GB2" and collecting):
            server.advance_auctions()
            with auction_transaction(server, (key,)) as auction:
                replies = auction.handle(key, payload)
        elif (
            prefix != b"GTM0GB0"
            and not collecting
            and (
                prefix == b"GTM0G70"
                or server.prize.is_active(key)
                or server.prize.can_replay_quit(key, payload)
            )
        ):
            with prize_transaction(server, key) as prize:
                replies = prize.handle(key, payload)
        if replies is not None:
            await reply(session, service_nick(SHOP_RAW), replies)
            return True
    if payload.startswith(b"GTM0P"):
        replies = character_profile_request(server, key, payload)
        if replies is not None:
            await reply(session, target, replies)
            return True
    if payload.startswith(b"GTM0R"):
        server.advance_rankings()
        replies = server.ranking.handle(key, payload)
        if replies is not None:
            await reply(session, target, replies)
            return True
    if target == service_nick(TRADE_RAW):
        responses = server.trade_router.entry(session, payload)
        if responses is not None:
            await server.trade_updates(responses)
            return True
    if target == service_nick(SHOP_RAW):
        responses = server.commerce.shop(key, payload)
        if responses is not None:
            await reply(session, target, responses)
            return True
    if ordinary_table is not None:
        return await ordinary_table.dispatch_table(session, payload)
    if ordinary_room is not None:
        account = server.economy.account_view(key)
        commands = parse(payload[5:]) if payload.startswith(b"GTM0L") else ()
        entering = len(commands) == 1 and commands[0].token == b"DE"
        async with AsyncExitStack() as entry:
            previous = None
            if entering:
                owner = server.table_for_peer(session)
                if owner is not None and owner.room.room_root is not ordinary_room.room.room_root:
                    # Recheck after acquiring the lock: another player may have
                    # started while this room-entry request was waiting.
                    await entry.enter_async_context(owner.match_lock)
                    if session.closed:
                        return True
                previous = validate_room_entry(
                    server, session, ordinary_room.room, allow_lobby_exit=True
                )
            was_member = session in ordinary_room.room.members
            responses = ordinary_room.room.handle(
                session,
                payload,
                authenticated_identity=(account.nn, account.player_id),
                authenticated_name=account.name,
            )
            if responses is not None:
                chat_updates = (
                    server.chat.leave(session, target)
                    if was_member and session not in ordinary_room.room.members
                    else []
                )
                if entering and session in ordinary_room.room.members:
                    # Only a successful different-room DE confirms navigation.
                    # Invalid/full destinations keep the old table reserved.
                    if previous is not None:
                        # Publish this destination sequence before waiting on
                        # old-table notifications; newer destination PD updates
                        # must not overtake its accepted DE.
                        await ordinary_room.publish_room(responses)
                        responses = []
                        if session.closed:
                            return True
                        if server.table_for_peer(session) is previous:
                            await previous.release_lobby(session)
                            if session.closed:
                                return True
                    await leave_other_rooms(server, session, key, ordinary_room.room)
                await ordinary_room.publish_room(responses)
                await server.chat_updates(chat_updates)
                if len(commands) == 1 and commands[0].token == b"PD":
                    table = server.table_for_peer(session)
                    if table is not None:
                        await table.roster_presence(session, ordinary_room.room.room_id)
                return True
    if payload == b"GTM0GE1000000@TeachDV=":
        await reply(session, target, [TEACH_DV_ANSWER])
        return True
    return False
