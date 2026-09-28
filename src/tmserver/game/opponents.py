"""Recovered original PlPrm NPC identities and local pool/hand construction.

Original local reference only; hosted ComGame menu orchestration is separate.
No historical server deck distribution or availability policy is implied.
"""

from dataclasses import dataclass

from tmserver.game.ai import Personality


class OpponentError(ValueError):
    pass


@dataclass(frozen=True)
class Opponent:
    npc_id: int
    record_points: int
    name_threshold: int
    rating: int
    personality: Personality
    pack: int
    name: bytes

    def known_name(self, collection_points):
        if type(collection_points) is not int or not 0 <= collection_points <= 65535:
            raise OpponentError("collection points must fit the original WORD")
        return self.name if collection_points >= self.name_threshold else None


@dataclass(frozen=True)
class PreparedOpponent:
    opponent: Opponent
    hand: tuple
    unused: tuple


SOURCE_SHA256 = "b32c71431bf5857c5d2f3134b4ef0be924a02dae4f371aa7c8e706506206602c"
DECODED_SHA256 = "356040c4c76d790db4a22447de7ef4eadba89ab99c6599df4988b2124772a7a3"
ROWS = (
    (1, 0, 0, 300, 65, 1, 50, 25, b"Flower Girl Natasha"),
    (2, 100, 10, 293, 67, 3, 51, 26, b"Lumberjack Mick"),
    (3, 135, 20, 290, 69, 1, 48, 27, b"Quina"),
    (4, 175, 30, 289, 70, 2, 45, 28, b"Bogart the Gladiator"),
    (5, 190, 50, 282, 70, 3, 42, 29, b"Agnes"),
    (6, 205, 75, 278, 73, 2, 40, 30, b"Queen Brahne"),
    (7, 220, 100, 272, 75, 4, 39, 31, b"Puppeteer Mina"),
    (8, 245, 150, 267, 76, 3, 37, 32, b"Bartender Bill"),
    (9, 260, 200, 259, 77, 1, 36, 33, b"Akbar the Hunter"),
    (10, 295, 250, 250, 78, 2, 35, 17, b"Steiner"),
    (11, 310, 300, 199, 80, 2, 34, 34, b"Jill"),
    (12, 345, 350, 192, 82, 3, 33, 14, b"Stiltzkin"),
    (13, 360, 400, 189, 83, 1, 32, 35, b"Fabius the Pirate"),
    (14, 400, 450, 182, 85, 2, 31, 16, b"Amarant"),
    (15, 425, 500, 177, 86, 3, 30, 36, b"Gardener Broidy"),
    (16, 455, 550, 170, 87, 4, 29, 37, b"Lani"),
    (17, 470, 600, 169, 88, 3, 28, 38, b"Eiko"),
    (18, 535, 650, 162, 89, 2, 27, 18, b"Zidane"),
    (19, 560, 700, 156, 90, 1, 26, 39, b"Chef Pierre"),
    (20, 590, 750, 149, 92, 3, 25, 40, b"Freya"),
    (21, 615, 800, 145, 93, 2, 20, 41, b"Revolutionary Carol"),
    (22, 675, 850, 143, 94, 3, 19, 11, b"Vivi"),
    (23, 730, 900, 138, 94, 2, 17, 13, b"Trade King Gohn"),
    (24, 785, 950, 137, 95, 2, 16, 19, b"Kuja"),
    (25, 840, 1000, 135, 97, 3, 15, 15, b"Garnet"),
    (26, 865, 1050, 131, 98, 3, 12, 42, b"Artania"),
    (27, 895, 1100, 129, 100, 2, 8, 43, b"Beatrix"),
    (28, 910, 1250, 126, 100, 3, 5, 44, b"Doctor Tot"),
    (29, 950, 1500, 111, 100, 2, 3, 10, b"Pilot Erin"),
    (30, 1000, 2000, 100, 100, 0, 0, 12, b"Regent Cid"),
)

OPPONENTS = tuple(
    Opponent(i, points, threshold, rating, Personality(score, preference, chance), pack, name)
    for i, points, threshold, rating, score, preference, chance, pack, name in ROWS
)


def opponent(npc_id):
    if type(npc_id) is not int or not 1 <= npc_id <= 30:
        raise OpponentError("named NPC selector must be1..30")
    return OPPONENTS[npc_id - 1]


def prepare_opponents(npc_ids, catalog, raw_rng, guild=0):
    """Generate every NPC pool before choosing hands, as in the local menu.

    100EC14F..1C0 generates ten cards for each selected NPC, then the later
    100D9DE0 chooser takes pool0 and four random remaining cards per seat.
    These isolated stages run consecutively here. Intervening client scene,
    board-generation and optional frame-poll RNG draws are not reproduced;
    historical hosted scheduling remains unknown.
    """
    try:
        npc_ids = tuple(npc_ids)
    except TypeError as exc:
        raise OpponentError("one or two named NPC selectors are required") from exc
    if not 1 <= len(npc_ids) <= 2:
        raise OpponentError("one or two named NPC selectors are required")
    selected = tuple(opponent(npc_id) for npc_id in npc_ids)
    if len(set(npc_ids)) != len(npc_ids):
        raise OpponentError("named NPC selectors must be distinct")
    if type(guild) is not int or guild not in range(4):
        raise OpponentError("guild/filter must be0..3")

    def draw():
        value = raw_rng()
        if type(value) is not int or not 0 <= value <= 32767:
            raise OpponentError("CRT draw must be0..32767")
        return value

    pools = [
        [catalog.generate(catalog.select(npc.pack, guild, draw), draw) for _ in range(10)]
        for npc in selected
    ]
    prepared = []
    for npc, pool in zip(selected, pools, strict=True):
        hand = [pool.pop(0)]
        for _ in range(4):
            hand.append(pool.pop(draw() % len(pool)))
        prepared.append(PreparedOpponent(npc, tuple(hand), tuple(pool)))
    return tuple(prepared)


def prepare_opponent(npc_id, catalog, raw_rng, guild=0):
    """Single-NPC form, retaining the original pool10 then selection5 stream."""
    return prepare_opponents((npc_id,), catalog, raw_rng, guild)[0]
