"""Explicit raw POL identities and their TM0 game representation."""

from tmserver.pol_wire import polid_scramble, polid_unscramble

GAME_KEY = 0xB62DC8D0E4BCBB90
ROOM_RAW = 0x100
TABLE_RAW = 0x200
SHOP_RAW = 0x300
TRADE_RAW = 0x400


def game_id(raw):
    if not 0 <= raw <= 0x1FFFFFFFFFF:
        raise ValueError("raw POL identity exceeds 41 bits")
    return raw ^ GAME_KEY


def service_nick(raw):
    return polid_scramble(raw)


def player_id(nick):
    return game_id(polid_unscramble(nick))
