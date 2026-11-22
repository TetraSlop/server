"""32-bit: dump per-case native results for every vector set, plus growth at 24 bits."""
import ctypes, json, sys
from research.probe_original_rules import (Draws, SPECIAL_VECTORS, battle_cases, move_cases,
    snapshot, verify_vectors)
from research.native_primitive_cases import growth_cases, target_cases
from tmserver.game import ai, rules, special_rules
from tmserver.game.original import native as N

image, bridge, out = sys.argv[1:4]
b = N.NativeRules(image, bridge)
print(verify_vectors(b))

def grow(card, baseline, mode, value, precision):
    card = rules.Card.from_card(card)
    vals = tuple(getattr(baseline, f) for f in ("attack", "physical", "magical", "points", "price"))
    req = N._GrowthRequest((N._U32 * 8)(*card.full()), (N._U32 * 5)(*vals), mode & 255, value & 255, precision)
    res = N._GrowthResult()
    assert b._bridge.tm_rules_growth(ctypes.byref(req), ctypes.byref(res)) == 0
    changed = None if res.changed == -1 else ("attack", "kind", "physical", "magical")[res.changed - 1]
    return snapshot(rules.GrowthResult(rules.Card(*res.card), res.sale, changed))

o = {"battles": [], "moves": [], "growth": [], "growth24": [], "targets": [], "special": []}
for name, fn, cases in (("battles", b.calculate_battle, battle_cases()), ("moves", b.choose_move, move_cases())):
    for args, options, values in cases:
        d = Draws(values)
        o[name].append({"result": snapshot(fn(*args, d, **options)), "draws": list(b.last_draws)})
for card, baseline, mode, value, precision in growth_cases():
    r = grow(card, baseline, mode, value, precision)
    assert r == snapshot(b.grow_card(card, baseline, mode, value, x87_precision=precision))
    o["growth"].append({"result": r, "draws": []})
    o["growth24"].append({"result": grow(card, baseline, mode, value, 24), "draws": []})
for targets, size, values in target_cases():
    d = Draws(values)
    o["targets"].append({"result": b.choose_target(targets, d, board_size=size), "draws": list(b.last_draws)})
json.dump(o, open(out, "w"))
print({k: len(v) for k, v in o.items()})
