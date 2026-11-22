"""Register an account with LOGIN against a running server, then sign in to the game with it."""

import argparse
import asyncio
import re
import secrets

from tests.support.irc import Client
from tmserver.protocol.wire import polid_from_string, polid_scramble

GRANTED = re.compile(rb":srv 900 (\w{8}) ([0-9a-f]{14}) :(created|signed_in)\Z")


async def check(port):
    username = b"smoke" + secrets.token_hex(4).encode()
    reply, _ = await Client().login(port, username, b"smoke-password")
    match = GRANTED.match(reply)
    if not match:
        raise SystemExit(f"LOGIN refused: {reply!r}")
    key = polid_scramble(polid_from_string(match.group(1)))
    client = Client()
    await client.open(port, key, match.group(2))
    try:
        welcome = await client.read()
    finally:
        await client.close()
    if b" 422 " not in welcome:
        raise SystemExit(f"game sign-in refused: {welcome!r}")
    print(f"LOGIN created {username.decode()} and the game accepted it")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=51241)
    asyncio.run(check(parser.parse_args().port))


if __name__ == "__main__":
    main()
