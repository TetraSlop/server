"""Pre-e3192ab Python port behind the current backend interface (phase 1 spike)."""
from dataclasses import astuple
from oldrules import ai as oai, rules as orules, special_rules as osp
from tmserver.game import ai, rules


class _Track:
    def __init__(self, draw, owner):
        self.draw, self.owner = draw, owner

    def __call__(self):
        v = self.draw()
        self.owner._last_draws.append(v)
        return v


def _move(m):
    return None if m is None else ai.Move(m.hand_index, m.field)


def _pers(p):
    return oai.Personality(p.score_chance, p.preference, p.preference_chance)


def _errors(fn):
    def wrapped(self, *a, **k):
        self._last_draws = []
        try:
            return fn(self, *a, **k)
        except oai.AIError as e:
            raise ai.AIError(str(e)) from e
        except orules.RulesError as e:
            raise rules.RulesError(str(e)) from e
    return wrapped


class OldRules:
    _last_draws = []

    @property
    def last_draws(self):
        return tuple(self._last_draws)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    @_errors
    def calculate_battle(self, attacker, defender, draw, **kw):
        r = orules.calculate_battle(attacker, defender, _Track(draw, self), **kw)
        return rules.BattleResult(rules.BattleSide(*astuple(r.attacker)),
                                  rules.BattleSide(*astuple(r.defender)), r.raw_draws)

    @_errors
    def choose_move(self, hand, board, owner, raw_rng, personality=ai.Personality()):
        board = tuple(oai.BLOCKED if c is ai.BLOCKED else c for c in board)
        return _move(oai.choose_move(hand, board, owner, _Track(raw_rng, self), _pers(personality)))

    @_errors
    def choose_special_move(self, hand, board, active, raw_rng, personality=None):
        cells = tuple(osp.Cell(model=c.model, arrows=c.arrows, owner=c.owner, property=c.property,
                               direction=c.direction) for c in board)
        return _move(osp.choose_special_move(hand, cells, active, _Track(raw_rng, self),
                                             _pers(personality or ai.Personality())))

    @_errors
    def grow_card(self, card, baseline, mode, random_value, *, x87_precision=64):
        r = orules.grow_card(card, baseline, mode, random_value, x87_precision=x87_precision)
        return rules.GrowthResult(rules.Card(*astuple(r.card)), r.sale_value, r.changed_field)

    @_errors
    def choose_target(self, candidates, raw_rng, *, board_size=16):
        return oai.choose_target(candidates, _Track(raw_rng, self), board_size=board_size)


INSTANCE = OldRules()
import tmserver.game.original.runtime as runtime
runtime.default_rules = lambda: INSTANCE
runtime.configured_rules = lambda image=None, bridge=None: OldRules()
