"""Windows x86 bridge to original battle, growth, target and board AI code.

Native contexts share one mapped image and allow one operation process-wide;
the bridge gate rejects concurrent and reentrant calls with status 1, and its
valid() checks reject out-of-domain requests with status 2 before any draw.
Close contexts explicitly, preferably with a ``with NativeRules(...)`` block.
"""

import ctypes
from dataclasses import astuple
import struct
import sys
from pathlib import Path

from tmserver.cards.model import card_values
from tmserver.data.resources import read_json
from tmserver.game import ai, rules
from tmserver.game.original.image import OriginalCodeError, read_image

_U32 = ctypes.c_uint32
_I32 = ctypes.c_int32
_Random = ctypes.CFUNCTYPE(_U32, ctypes.c_void_p, ctypes.POINTER(_U32))
_CARD_FIELDS = (
    "attack",
    "type",
    "physical_defense",
    "magical_defense",
    "base_card_points",
    "price_tier",
    "category_6",
    "category_7",
)


def _card_record(card):
    """Rebuild the 12-byte CardPrm record; bytes 8-9 are FFFF in every record."""
    return struct.pack("<8BHH", *(card[k] for k in _CARD_FIELDS), 0xFFFF, card["name_reference"])


class _BattleRequest(ctypes.Structure):
    _fields_ = [
        ("attacker", _U32 * 4),
        ("defender", _U32 * 4),
        ("attacker_modifier", _I32),
        ("defender_modifier", _I32),
        ("attacker_property", _U32),
        ("defender_property", _U32),
    ]


class _BattleResult(ctypes.Structure):
    _fields_ = [("attacker", _U32 * 4), ("defender", _U32 * 4)]


class _HandCard(ctypes.Structure):
    _fields_ = [("model", _U32), ("arrows", _U32)]


class _BoardCell(ctypes.Structure):
    _fields_ = [
        ("model", _U32),
        ("arrows", _U32),
        ("owner", _U32),
        ("property", _U32),
        ("direction", _U32),
    ]


class _MoveRequest(ctypes.Structure):
    _fields_ = [
        ("count", _U32),
        ("owner", _U32),
        ("board_size", _U32),
        ("personality", _U32 * 3),
        ("hand", _HandCard * 5),
        ("board", _BoardCell * 25),
    ]


class _MoveResult(ctypes.Structure):
    _fields_ = [("hand", _I32), ("field", _I32)]


class _GrowthRequest(ctypes.Structure):
    _fields_ = [
        ("card", _U32 * 8),
        ("baseline", _U32 * 5),
        ("mode", _U32),
        ("random", _U32),
        ("precision", _U32),
    ]


class _GrowthResult(ctypes.Structure):
    _fields_ = [("card", _U32 * 8), ("sale", _U32), ("changed", _I32)]


class _TargetRequest(ctypes.Structure):
    _fields_ = [("count", _U32), ("board_size", _U32), ("candidates", _U32 * 8)]


class _TargetResult(ctypes.Structure):
    _fields_ = [("field", _I32)]


def _check_status(status, operation):
    if status:
        reason = {
            1: "original code is already executing in this process",
            2: "invalid bridge input",
            3: "native image is not initialized",
            4: "native image initialization failed",
            0xE0010001: "random callback failed",
        }.get(status, f"native exception 0x{status:08X}")
        raise OriginalCodeError(f"Cannot {operation}: {reason}")


def _load_bridge(path):
    try:
        bridge = ctypes.CDLL(str(Path(path).resolve()))
        bridge.tm_rules_abi.argtypes = []
        bridge.tm_rules_abi.restype = _U32
        version = bridge.tm_rules_abi()
        if version != 3:
            raise OriginalCodeError(f"Unsupported native rules ABI {version}; expected 3")
        signatures = (
            ("tm_rules_open", [ctypes.c_void_p, _U32, ctypes.c_void_p, _U32]),
            ("tm_rules_close", []),
            (
                "tm_rules_battle",
                [
                    ctypes.POINTER(_BattleRequest),
                    _Random,
                    ctypes.c_void_p,
                    ctypes.POINTER(_BattleResult),
                ],
            ),
            (
                "tm_rules_move",
                [
                    ctypes.POINTER(_MoveRequest),
                    _Random,
                    ctypes.c_void_p,
                    ctypes.POINTER(_MoveResult),
                ],
            ),
            (
                "tm_rules_growth",
                [ctypes.POINTER(_GrowthRequest), ctypes.POINTER(_GrowthResult)],
            ),
            (
                "tm_rules_target",
                [
                    ctypes.POINTER(_TargetRequest),
                    _Random,
                    ctypes.c_void_p,
                    ctypes.POINTER(_TargetResult),
                ],
            ),
        )
        for name, arguments in signatures:
            function = getattr(bridge, name)
            function.argtypes = arguments
            function.restype = _U32
    except (OSError, AttributeError) as exc:
        raise OriginalCodeError(f"Cannot load the native rules bridge: {exc}") from exc
    return bridge


class NativeRules:
    def __init__(self, image: str | Path, bridge: str | Path):
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 4:
            raise OriginalCodeError("The native rules bridge requires 32-bit Windows Python")
        self._closed = True
        self._last_draws = ()
        self._bridge = _load_bridge(bridge)
        data = read_image(image)
        catalog = b"".join(map(_card_record, read_json("economy_catalog.json")["cards"]))
        _check_status(
            self._bridge.tm_rules_open(data, len(data), catalog, len(catalog)), "open native rules"
        )
        self._closed = False

    @property
    def last_draws(self) -> tuple[int, ...]:
        """Successful draws consumed by the last operation, including an interrupted one."""
        return self._last_draws

    def close(self):
        if not self._closed:
            _check_status(self._bridge.tm_rules_close(), "close native rules")
            self._closed = True

    def __enter__(self):
        if self._closed:
            raise OriginalCodeError("This native rules context is closed")
        return self

    def __exit__(self, *_):
        self.close()

    def _start(self):
        if self._closed:
            raise OriginalCodeError("This native rules context is closed")
        self._last_draws = ()

    def _call(self, function, request, result, draw, maximum):
        """Run one bridge call; a draw is recorded only once it is in range."""
        self._start()
        draws, failures = [], []

        @_Random
        def random(_user, output):
            if failures:
                return 1
            try:
                value = draw()
                if type(value) is not int or not 0 <= value <= maximum:
                    raise OriginalCodeError(f"Random draw must be an integer in 0..{maximum}")
                draws.append(value)
                output[0] = value
                return 0
            except BaseException as exc:
                failures.append(exc)
                return 1

        try:
            status = function(ctypes.byref(request), random, None, ctypes.byref(result))
        finally:
            self._last_draws = tuple(draws)
        if failures:
            raise failures[0]
        _check_status(status, "execute native rules")

    def calculate_battle(
        self,
        attacker,
        defender,
        draw,
        *,
        attacker_modifier=0,
        defender_modifier=0,
        attacker_property=0,
        defender_property=0,
    ) -> rules.BattleResult:
        request = _BattleRequest(
            *((_U32 * 4)(c.attack, c.kind, c.physical, c.magical) for c in (attacker, defender)),
            attacker_modifier,
            defender_modifier,
            attacker_property,
            defender_property,
        )
        result = _BattleResult()
        self._call(self._bridge.tm_rules_battle, request, result, draw, 0x7FFFFFFF)
        return rules.BattleResult(
            rules.BattleSide(*result.attacker), rules.BattleSide(*result.defender), self.last_draws
        )

    def choose_move(
        self, hand, board, owner, raw_rng, personality=ai.Personality()
    ) -> ai.Move | None:
        cells = [
            _BoardCell(0xFFFF, 0, 8, 0, 0)
            if cell is None
            else _BoardCell(0x8000, 0, 9, 0, 0)
            if cell is ai.BLOCKED
            else _BoardCell(cell.card.model, cell.card.arrows, cell.owner, 0, 0)
            for cell in board
        ]
        return self._choose_move(hand, cells, owner, raw_rng, personality)

    def choose_special_move(
        self, hand, board, active, raw_rng, personality=None
    ) -> ai.Move | None:
        """Choose on a 4x4 or 5x5 board, preserving tile properties and spinners."""
        cells = [
            _BoardCell(cell.model, cell.arrows, cell.owner, cell.property, cell.direction)
            for cell in board
        ]
        return self._choose_move(hand, cells, active, raw_rng, personality or ai.Personality())

    def _choose_move(self, hand, cells, owner, raw_rng, personality):
        self._start()
        hand = tuple(hand)
        if not hand or all(cell.owner != 8 for cell in cells):
            return None
        request = _MoveRequest()
        # Oversized hands and boards keep their real counts so valid() rejects them.
        request.count, request.owner, request.board_size = len(hand), owner, len(cells)
        request.personality[:] = (
            personality.score_chance,
            personality.preference,
            personality.preference_chance,
        )
        for index, card in enumerate(hand[:5]):
            request.hand[index] = _HandCard(card.model, card.arrows)
        for index, cell in enumerate(cells[:25]):
            request.board[index] = cell
        result = _MoveResult()
        self._call(self._bridge.tm_rules_move, request, result, raw_rng, 32767)
        if (
            not 0 <= result.hand < len(hand)
            or not 0 <= result.field < len(cells)
            or cells[result.field].owner != 8
        ):
            raise OriginalCodeError("Original AI returned an invalid immediate move")
        return ai.Move(result.hand, result.field)

    def grow_card(
        self, card, baseline: rules.GrowthBaseline, mode, random_value, *, x87_precision=64
    ) -> rules.GrowthResult:
        request = _GrowthRequest(
            (_U32 * 8)(*card_values(card)),
            (_U32 * 5)(*astuple(baseline)),
            mode & 255,
            random_value & 255,
            x87_precision,
        )
        self._start()
        result = _GrowthResult()
        _check_status(
            self._bridge.tm_rules_growth(ctypes.byref(request), ctypes.byref(result)),
            "grow native card",
        )
        if result.changed not in (-1, 1, 2, 3, 4):
            raise OriginalCodeError("Original growth returned an invalid changed field")
        changed = (
            None
            if result.changed == -1
            else ("attack", "kind", "physical", "magical")[result.changed - 1]
        )
        return rules.GrowthResult(rules.Card(*result.card), result.sale, changed)

    def choose_target(self, candidates, raw_rng, *, board_size=16) -> int | None:
        candidates = tuple(candidates)
        request = _TargetRequest(len(candidates), board_size, (_U32 * 8)(*candidates[:8]))
        result = _TargetResult()
        self._call(self._bridge.tm_rules_target, request, result, raw_rng, 32767)
        if not candidates and result.field == -1:
            return None
        if result.field not in candidates:
            raise OriginalCodeError("Original AI returned an invalid immediate target")
        return result.field
