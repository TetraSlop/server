"""Pure original-client board effects; no hosted placement/distribution policy.

Source: SPECIAL_CARD_EFFECTS.md / COMBAT_SEQUENCE.md. Ordinary16/25-cell
boards, valid F0..18, and nonnegative spinner display strengths only. Card
payload/identity are opaque immutable objects; no inventory or RNG is owned.
"""

from dataclasses import dataclass, replace


class SpecialRulesError(ValueError):
    pass


DIRECTIONS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


@dataclass(frozen=True)
class Cell:
    model: int = 0xFFFF
    card: object = None
    base_owner: int = 8
    owner: int = 8
    arrows: int = 0
    property: int = 0
    modifier: int = 0
    direction: int = 0
    marker: int = 0
    identity: object = None

    def __post_init__(self):
        for name, hi in (
            ("model", 65535),
            ("base_owner", 8),
            ("owner", 8),
            ("arrows", 255),
            ("property", 255),
            ("direction", 7),
            ("marker", 255),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= hi:
                raise SpecialRulesError(name + " outside supported native domain")
        if type(self.modifier) is not int or not -128 <= self.modifier <= 127:
            raise SpecialRulesError("modifier must be signed byte")

    @classmethod
    def ordinary(cls, card, owner, identity=None, **effects):
        if not 0 <= card.model < 0x8000:
            raise SpecialRulesError("ordinary card model required")
        return cls(
            model=card.model,
            card=card,
            base_owner=owner,
            owner=owner,
            arrows=card.arrows,
            identity=identity,
            **effects,
        )


@dataclass(frozen=True)
class StartBoard:
    cells: tuple[Cell, ...]
    fields: tuple[int, ...]
    blocks: int
    effects: int


@dataclass(frozen=True)
class EffectResult:
    cells: tuple[Cell, ...]
    continue_combat: bool


def _shape(board):
    size = {16: 4, 25: 5}.get(len(board))
    if size is None or any(not isinstance(c, Cell) for c in board):
        raise SpecialRulesError("expected16 or25 native board cells")
    return size, size - 2


def _index(board, index):
    if type(index) is not int or not 0 <= index < len(board):
        raise SpecialRulesError("board index outside board")


def _active(board, active):
    _, players = _shape(board)
    if type(active) is not int or not 0 <= active < players:
        raise SpecialRulesError("active player outside board mode")
    return players


def _random(values):
    values = tuple(values)
    if len(values) != 8 or any(type(v) is not int or not 0 <= v <= 255 for v in values):
        raise SpecialRulesError("shared R must contain eight bytes")
    return values


def neighbors(board, index, wrapping=False):
    size, _ = _shape(board)
    _index(board, index)
    x, y = index % size, index // size
    for direction, (dx, dy) in enumerate(DIRECTIONS):
        xx, yy = x + dx, y + dy
        if wrapping:
            yield direction, (yy % size) * size + (xx % size)
        elif 0 <= xx < size and 0 <= yy < size:
            yield direction, yy * size + xx


def start_board(fields):
    fields = tuple(fields)
    if len(fields) not in (16, 25) or any(type(f) is not int or not 0 <= f <= 18 for f in fields):
        raise SpecialRulesError("StartData requires16/25 F values in0..18")
    cells = []
    for f in fields:
        if not f:
            cells.append(Cell())
            continue
        model = 0x8000 + f if f <= 8 else 0x8009 if f <= 16 else 0x800A if f == 17 else 0x800B
        prop = {3: 1, 4: 2, 5: 3, 6: 0x10, 7: 0x20, 8: 0x30, 17: 0x40, 18: 0x50}.get(f, 0)
        direction = f - 9 if 9 <= f <= 16 else 0
        arrows = (1 << direction) if 9 <= f <= 16 else 255 if f in (6, 7, 8, 17, 18) else 0
        cells.append(
            Cell(
                model=model,
                base_owner=4,
                owner=8 if 3 <= f <= 5 else 4,
                arrows=arrows,
                property=prop,
                direction=direction,
            )
        )
    effects = sum(3 <= f <= 5 for f in fields)
    return StartBoard(tuple(cells), fields, sum(f != 0 for f in fields) - effects, effects)


def place(board, index, card, owner, identity=None):
    _active(board, owner)
    _index(board, index)
    if board[index].owner != 8:
        raise SpecialRulesError("placement requires vacant/current-owner8 cell")
    output = list(board)
    output[index] = Cell.ordinary(card, owner, identity, property=board[index].property)
    return tuple(output)


def effective_arrows(cell):
    return 255 if cell.property & 15 == 3 else cell.arrows


def classify(board, source, active):
    players = _active(board, active)
    _index(board, source)
    output = list(board)
    arrows = effective_arrows(board[source])
    for direction, index in neighbors(board, source):
        if not arrows & (1 << direction):
            continue
        cell = output[index]
        if cell.owner < players:
            if cell.owner == active:
                continue
            if effective_arrows(cell) & (1 << ((direction + 4) & 7)):
                output[index] = replace(cell, marker=1)
            elif cell.marker != 1:
                output[index] = replace(cell, marker=2)
        elif cell.owner == 4 and cell.marker != 2 and cell.arrows:
            output[index] = replace(cell, marker=1)
    return tuple(output)


def combo(board, source, winner, loser):
    _active(board, winner)
    _active(board, loser)
    _index(board, source)
    output = list(board)
    output[source] = replace(output[source], marker=0)
    for direction, index in neighbors(board, source):
        if effective_arrows(board[source]) & (1 << direction) and output[index].owner == loser:
            output[index] = replace(output[index], owner=winner, marker=0)
    return tuple(output)


def direct_captures(board, active):
    _active(board, active)
    return tuple(
        replace(c, owner=active, marker=0) if c.marker == 2 and c.model < 0x8009 else c
        for c in board
    )


def ownership_beneficiary(active, players, r1):
    if players not in (2, 3) or not 0 <= active < players or not 0 <= r1 <= 255:
        raise SpecialRulesError("invalid shared ownership parameters")
    return active if r1 < 179 else (active + (2 if players == 3 and r1 >= 217 else 1)) % players


def modify_strength(board, active, delta):
    _active(board, active)
    if delta not in (-10, 10):
        raise SpecialRulesError("native modifier step must be +/-10")
    return tuple(
        replace(c, modifier=((c.modifier + delta + 128) & 255) - 128) if c.owner == active else c
        for c in board
    )


def adjacent_ownership(board, target, beneficiary):
    players = _active(board, beneficiary)
    _index(board, target)
    output = list(board)
    for _, index in neighbors(board, target):
        if output[index].owner < players:
            output[index] = replace(output[index], owner=beneficiary, marker=0)
    return tuple(output)


def shuffle(board, shared):
    _shape(board)
    shared = _random(shared)
    saved = [(i, c) for i, c in enumerate(board) if c.model < 0x8000]
    output = list(board)
    for i, _ in saved:
        output[i] = Cell()
    for old_index, cell in saved:
        candidates = [i for i, c in enumerate(output) if c.base_owner == 8]
        if not candidates:
            raise SpecialRulesError("no native shuffle candidate")
        target = candidates[shared[old_index & 7] % len(candidates)]
        output[target] = replace(cell, marker=0)
    return tuple(output)


def spinner_chain(board, target, active, displayed_start):
    """Full1E..22 cascade; range uses displayed A start, not final power."""
    players = _active(board, active)
    _index(board, target)
    if (
        board[target].model != 0x8009
        or type(displayed_start) is not int
        or not 0 <= displayed_start <= 32767
    ):
        raise SpecialRulesError("spinner needs ID8009 and nonnegative signed-WORD display start")
    size, _ = _shape(board)
    output = list(board)
    output[target] = replace(output[target], marker=3)
    distance = min(displayed_start // 30 + 1, players)
    while True:
        current = [i for i, c in enumerate(output) if c.model == 0x8009 and c.marker == 3]
        if not current:
            break
        for index in current:
            direction = (output[index].direction - 4) & 7
            dx, dy = DIRECTIONS[direction]
            x, y = index % size, index // size
            for step in range(1, distance + 1):
                j = ((y + dy * step) % size) * size + (x + dx * step) % size
                c = output[j]
                if c.owner < players and c.owner != active:
                    output[j] = replace(c, owner=active, marker=3)
                elif c.model == 0x8009 and c.marker not in (2, 3):
                    output[j] = replace(c, marker=4)
        # The presentation's second walk clears captured transient markers;
        # spinner3->2 /4->3 creates the next wave and forbids reactivation.
        output = [
            (
                replace(c, marker=2 if c.marker == 3 else 3 if c.marker == 4 else c.marker)
                if c.model == 0x8009
                else replace(c, marker=0) if c.marker == 3 else c
            )
            for c in output
        ]
    return tuple(output)


def apply_special(board, source, target, active, shared, *, displayed_start=0, replacement=None):
    """Apply a selected tile AFTER its BattleData. CBC supply is explicit."""
    players = _active(board, active)
    _index(board, source)
    _index(board, target)
    shared = _random(shared)
    tile = board[target]
    if tile.owner != 4:
        raise SpecialRulesError("special dispatch requires owner4")
    if tile.model == 0x8009:
        return EffectResult(spinner_chain(board, target, active, displayed_start), True)
    high = tile.property >> 4
    if high in (1, 2):
        output = modify_strength(board, active, -10 if high == 1 else 10)
    elif high == 3:
        output = adjacent_ownership(
            board, target, ownership_beneficiary(active, players, shared[1])
        )
        if output[source].owner != active:
            output = tuple(replace(c, marker=0) for c in output)
    elif high == 4:
        output = shuffle(board, shared)
        output = list(output)
        output[target] = Cell()
        return EffectResult(tuple(replace(c, marker=0) for c in output), False)
    elif high == 5:
        if replacement is None:
            raise SpecialRulesError("materializing tile requires explicit CBC card")
        output = list(board)
        output[target] = replace(Cell.ordinary(replacement, active), base_owner=5)
        return EffectResult(tuple(output), True)
    else:
        raise SpecialRulesError("target has no mapped high-nibble effect")
    output = list(output)
    output[target] = Cell()
    return EffectResult(tuple(output), True)


def finish_turn(board, source):
    """Clear markers, consume placed property01, rotate spinners even on a pass."""
    _shape(board)
    if source is not None:
        _index(board, source)
    output = []
    for i, c in enumerate(board):
        c = replace(c, marker=0, property=0 if i == source and c.property == 1 else c.property)
        if c.model == 0x8009:
            direction = (c.direction + 1) & 7
            c = replace(c, direction=direction, arrows=1 << direction)
        output.append(c)
    return tuple(output)


def choose_special_move(hand, board, active, raw_rng, personality=None):
    from tmserver.game.original.runtime import default_rules

    return default_rules().choose_special_move(hand, board, active, raw_rng, personality)
