"""Synthetic service metadata for the recovered native bootstrap.

Values describe our local dataset, not claimed historical retail values.
Reply keys and pipe-separated elements come from TM 0x100812e0 and 0x100ab080.
"""

from tmserver.protocol.messages import service_notice

from dataclasses import dataclass

from tmserver.economy.claim_transactions import auction_transaction, prize_transaction
from tmserver.events.routing import dispatch_event
from tmserver.protocol.identities import SHOP_RAW, TRADE_RAW, game_id, service_nick
from tmserver.profiles.options import save_options
from tmserver.profiles.transactions import character_profile_request
from tmserver.rooms.routing import leave_other_rooms, validate_room_entry
from tmserver.protocol.commands import parse


@dataclass(frozen=True)
class DirectoryVersion:
    directory: int = 1
    version: int = 1

    def pair(self):
        if not 0 <= self.directory <= 255 or not 0 <= self.version <= 65535:
            raise ValueError("directory/version outside native field width")
        return f"{self.directory}|{self.version}".encode()


def teach_dv_answer():
    local = DirectoryVersion()
    pair = local.pair()
    return (
        b"GTM0GE2000000@TeachDVAns=/D=1/V=1/AC="
        + pair
        + b"/RK="
        + pair
        + b"/Shm="
        + f"{game_id(SHOP_RAW):016X}".encode()
        + b"|"
        + pair
    )


async def dispatch(session, target, payload):
    # Reject unframeable service routes before any handler can commit state.
    service_notice(target, session.nick, b"")
    ordinary_table = session.server.ordinary_table_routes.get(target)
    ordinary_room = session.server.ordinary_room_routes.get(target)
    if payload[:7].upper() == b"GTM0G80":
        key = session.server.ensure_account(session.nick)
        with session.server.commerce.edit_account(key) as economy:
            answer = economy.initialize_profile(key, payload)
        await session.send(service_notice(target, session.nick, answer))
        session.server.log("player_initialized", session=session.id, target=target.decode("ascii"))
        return True
    event_table = session.server.event_matches.routes.get(target)
    if event_table is not None:
        key = session.server.ensure_account(session.nick)
        if payload.startswith(b"GTM0G420001"):
            view = session.server.economy.account_view(key)
            await session.server.chat_updates(
                session.server.chat.enter(
                    session, target, payload, identity=view.nn, name=view.name
                )
            )
            return True
        if payload.startswith(b"GTM0G84"):
            with session.server.commerce.edit_account(key) as economy:
                answer = save_options(economy, key, payload)
            await session.server.deliver(
                session, service_notice(target, session.nick, answer)
            )
            return True
        return await session.server.event_matches.dispatch(session, event_table[1], payload)
    if target in session.server.event_routes and await dispatch_event(session, target, payload):
        return True
    if payload.startswith(b"GTM0A"):
        key = session.server.ensure_account(session.nick)
        session.server.advance_auctions()
        with auction_transaction(session.server, (key,)) as auction:
            replies = auction.handle(key, payload)
        if replies is not None:
            for answer in replies:
                await session.send(
                    service_notice(target, session.nick, answer)
                )
            return True
    if (
        ordinary_table is not None or ordinary_room is not None or target == service_nick(SHOP_RAW)
    ) and payload[:7].upper() in (b"GTM0GB0", b"GTM0GB2", b"GTM0G70"):
        key = session.server.ensure_account(session.nick)
        replies = None
        if payload[:7].upper() == b"GTM0GB0" or session.server.auction.collection_active(key):
            session.server.advance_auctions()
            with auction_transaction(session.server, (key,)) as auction:
                replies = auction.handle(key, payload)
        elif (
            payload[:7].upper() == b"GTM0G70"
            or session.server.prize.is_active(key)
            or session.server.prize.can_replay_quit(key, payload)
        ):
            with prize_transaction(session.server, key) as prize:
                replies = prize.handle(key, payload)
        if replies is not None:
            source = service_nick(SHOP_RAW)
            for answer in replies:
                await session.send(
                    service_notice(source, session.nick, answer)
                )
            return True
    if payload.startswith(b"GTM0P"):
        key = session.server.ensure_account(session.nick)
        replies = character_profile_request(session.server, key, payload)
        if replies is not None:
            commands = parse(payload[5:])
            if commands[0].token == b"CR" and parse(replies[0][5:])[0].token == b"CS":
                record = session.server.character_profiles.records[key]
                session.tm_player = {
                    "id": session.server.economy.account_view(key).player_id,
                    "name": record[2].encode("latin1"),
                    "info": next((c.payload for c in commands if c.token == b"CI"), b""),
                    "server": target,
                }
            for answer in replies:
                await session.send(
                    service_notice(target, session.nick, answer)
                )
            return True
    if payload.startswith(b"GTM0R"):
        key = session.server.ensure_account(session.nick)
        session.server.advance_rankings()
        replies = session.server.ranking.handle(key, payload)
        if replies is not None:
            for answer in replies:
                await session.send(
                    service_notice(target, session.nick, answer)
                )
            return True
    if (ordinary_table is not None or ordinary_room is not None) and payload.startswith(
        b"GTM0G420001"
    ):
        key = session.server.ensure_account(session.nick)
        account = session.server.economy.account_view(key)
        replies = session.server.chat.enter(
            session, target, payload, identity=account.nn, name=account.name
        )
        await session.server.chat_updates(replies)
        return True
    if (
        ordinary_table is not None
        or ordinary_room is not None
        or target in {service_nick(raw) for raw in (SHOP_RAW, TRADE_RAW)}
    ) and payload.startswith(b"GTM0G84"):
        key = session.server.ensure_account(session.nick)
        with session.server.commerce.edit_account(key) as economy:
            answer = save_options(economy, key, payload)
        await session.send(service_notice(target, session.nick, answer))
        session.server.log("options_saved", session=session.id)
        return True
    if target == service_nick(TRADE_RAW):
        responses = session.server.trade_router.entry(session, payload)
        if responses is not None:
            await session.server.trade_updates(responses)
            session.server.log(
                "trade_service",
                session=session.id,
                replies=len(responses),
                state=session.server.trade.status(session),
            )
            return True
    if target == service_nick(SHOP_RAW):
        key = session.server.ensure_account(session.nick)
        responses = session.server.commerce.shop(key, payload)
        if responses is not None:
            for answer in responses:
                await session.send(
                    service_notice(target, session.nick, answer)
                )
            session.server.log(
                "shop_service",
                session=session.id,
                replies=len(responses),
                balance=session.server.economy.balance(key),
                cards=len(session.server.economy.inventory(key)),
            )
            return True
    if ordinary_table is not None:
        return await ordinary_table.dispatch_table(session, payload)
    if ordinary_room is not None:
        key = session.server.ensure_account(session.nick)
        account = session.server.economy.account_view(key)
        commands = parse(payload[5:]) if payload.startswith(b"GTM0L") else ()
        entering = len(commands) == 1 and commands[0].token == b"DE"
        if entering:
            validate_room_entry(session.server, session, ordinary_room.room)
        was_member = session in ordinary_room.room.members
        responses = ordinary_room.room.handle(
            session,
            payload,
            authenticated_identity=(account.nn, account.player_id),
            authenticated_name=account.name,
        )
        if responses is not None:
            chat_updates = (
                session.server.chat.leave(session, target)
                if was_member and session not in ordinary_room.room.members
                else []
            )
            if entering and session in ordinary_room.room.members:
                await leave_other_rooms(session.server, session, key, ordinary_room.room)
            await ordinary_room.publish_room(responses)
            await session.server.chat_updates(chat_updates)
            if len(commands) == 1 and commands[0].token == b"PD":
                table = session.server.table_for_peer(session)
                if table is not None:
                    await table.roster_presence(session, ordinary_room.room.room_id)
            session.server.log(
                "room_service",
                session=session.id,
                sequence=ordinary_room.room.sequence,
                replies=len(responses),
            )
            return True
    if payload == b"GTM0GE1000000@TeachDV=":
        answer = teach_dv_answer()
        await session.send(service_notice(target, session.nick, answer))
        session.server.log(
            "teach_dv_answer",
            session=session.id,
            target=target.decode("ascii"),
            directory=1,
            version=1,
        )
        return True
    return False
