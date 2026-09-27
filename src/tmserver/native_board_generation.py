"""Exact ordinary local board generator100C0F16..100C11E5.

This is the original client's simulation policy, not historical hosted policy.
The caller chooses raw CRT seed/stream and when to use generated versus fixed
boards. No assets, persistence, networking or presentation are owned here.
"""

from dataclasses import dataclass


class BoardGenerationError(ValueError):
    pass


# Exact100-entry lookup tables, including the trailing15 zero bytes in3p E.
# Original100C0F33/100C0FA7 index these by raw_rand()%100 and mode*100.
BLOCK_COUNTS = (
    (0,) + (1,) * 2 + (2,) * 3 + (3,) * 5 + (4,) * 9 + (5,) * 30 + (6,) * 50,
    (0, 1, 2)
    + (3,) * 3
    + (4,) * 2
    + (5,) * 3
    + (6,) * 9
    + (7,) * 15
    + (8,) * 15
    + (9,) * 25
    + (10,) * 25,
)
EFFECT_COUNTS = (
    (0,) * 3 + (1,) * 20 + (2,) * 70 + (3,) * 5 + (4,) * 2,
    (0,) * 3 + (1,) * 2 + (2,) * 3 + (3,) * 70 + (4,) * 3 + (5,) * 2 + (6,) * 2 + (0,) * 15,
)


@dataclass(frozen=True)
class GeneratedBoard:
    fields: tuple[int, ...]
    blocks: int
    effects: int
    spinners: int
    active: int
    shared: tuple[int, ...]


def generate_board(player_count, raw_rng, *, st=0, cb=0, ca=0):
    """OriginalG.171 local simulation with ordinary role0 and native rule bits.

    F1/2 are walls; cb optionally substitutes6/7/8/17 among nonspinner blocks;
    ca turns floor(B/4) blocks into directional9..16; st adds low-property3..5.
    Selection is without replacement, rescanning ascending empty F indices.
    The original emits no CBC18 in this ordinary generator.
    """
    if type(player_count) is not int or player_count not in (2, 3):
        raise BoardGenerationError("ordinary board needs two or three players")
    if any(type(v) is not int or v not in (0, 1) for v in (st, cb, ca)):
        raise BoardGenerationError("st/cb/ca must be native0/1 flags")

    def draw():
        value = raw_rng()
        if type(value) is not int or not 0 <= value <= 32767:
            raise BoardGenerationError("raw_rng must return CRT rand values0..32767")
        return value

    mode = player_count - 2
    blocks = BLOCK_COUNTS[mode][draw() % 100]
    spinners = blocks // 4 if ca else 0
    effects = EFFECT_COUNTS[mode][draw() % 100] if st else 0
    fields = [0] * (player_count + 2) ** 2
    active = draw() % player_count

    def empty():
        return [i for i, value in enumerate(fields) if value == 0]

    for _ in range(blocks - spinners):
        candidates = empty()
        if cb and draw() % 100 < 50:
            value = draw() % 100
            field = 6 if value < 25 else 7 if value < 50 else 8 if value < 75 else 17
        else:
            field = (draw() & 1) + 1
        fields[candidates[draw() % len(candidates)]] = field
    for _ in range(spinners):
        candidates = empty()
        index = candidates[draw() % len(candidates)]
        fields[index] = (draw() & 7) + 9
    for _ in range(effects):
        candidates = empty()
        value = draw() % 100
        field = 3 if value < 25 else 4 if value < 75 else 5
        fields[candidates[draw() % len(candidates)]] = field
    shared = tuple(draw() & 255 for _ in range(8))
    return GeneratedBoard(tuple(fields), blocks, effects, spinners, active, shared)
