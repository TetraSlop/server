"""Headless child replacing tmloader in launcher tests; never loads native DLLs."""

import argparse
import asyncio

from tests.profiles.test_options_service import request
from tests.profiles.test_profile_accounts import file_request, transact
from tests.support.irc import Client
from tmserver.economy.models import PROFILE_SIZE
from tmserver.protocol.identities import SHOP_RAW, player_id, service_nick
from tmserver.network.local_accounts import account_specs, display_name


async def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-port", type=int)
    parser.add_argument("--profile-port", type=int)
    parser.add_argument("--pol-id")
    args, _ = parser.parse_known_args()
    key, (_, _, player) = next(
        (key, value) for key, value in account_specs(3).items() if value[0].decode() == args.pol_id
    )
    client = Client()
    try:
        await client.open(args.game_port, account=key.encode())
        assert b" 422 " in await client.read()
        payload = request(player_id(key.encode()), name=display_name(player))
        await client.send(b"NOTICE " + service_nick(SHOP_RAW) + b" :" + payload)
        assert b"@OptAns=/Ans=1" in await client.read()
        error, profile = await transact(
            args.profile_port,
            key,
            3,
            0,
            file_request(),
            context=(client.cipher, client.iv),
            account_id=args.pol_id.encode(),
        )
        assert error == 0 and len(profile) == PROFILE_SIZE + 4 and profile[0xB0] == 1
        await asyncio.sleep(player * 0.1)
    finally:
        if hasattr(client, "writer"):
            await client.close()


if __name__ == "__main__":
    asyncio.run(run())
