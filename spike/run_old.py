"""Run the pre-e3192ab Python port against the frozen vectors, case by case."""
import json, sys
from collections import Counter
from oldrules import ai as oai, rules as orules, special_rules as osp
from research.probe_original_rules import (Draws, SPECIAL_VECTORS, VECTORS, battle_cases,
    move_cases, snapshot, vector_digest)
from research.native_primitive_cases import growth_cases, target_cases
from tmserver.game import ai

oracle = json.load(open(sys.argv[1]))

def ordinary(board):
    return tuple(oai.BLOCKED if c is ai.BLOCKED else c for c in board)

def run(name, fn, cases, wrap=lambda r: r):
    records, fails = [], []
    for i, (args, options, values) in enumerate(cases):
        d = Draws(values)
        try:
            r = {"result": snapshot(wrap(fn(*args, d, **options))), "draws": list(d.used)}
        except Exception as e:
            r = {"error": f"{type(e).__name__}: {e}"}
        records.append(r)
        if r != oracle[name][i]:
            fails.append((i, r, oracle[name][i]))
    return records, fails

report = {}
recs, f = run("battles", orules.calculate_battle, battle_cases())
report["battles"] = (len(recs) - len(f), len(recs), vector_digest(recs) == VECTORS["groups"]["battles"], f[:3])
def move(hand, board, owner, draw, personality):
    p = oai.Personality(personality.score_chance, personality.preference, personality.preference_chance)
    return oai.choose_move(hand, ordinary(board), owner, draw, p)
recs, f = run("moves", move, move_cases())
report["moves"] = (len(recs) - len(f), len(recs), vector_digest(recs) == VECTORS["groups"]["moves"], f[:3])
gr = [], [], []
for prec_mode in ("recorded", 24):
    recs, fails = [], []
    key = "growth" if prec_mode == "recorded" else "growth24"
    for i, (card, base, mode, value, precision) in enumerate(growth_cases()):
        try:
            r = {"result": snapshot(orules.grow_card(card, base, mode, value,
                x87_precision=precision if prec_mode == "recorded" else 24)), "draws": []}
        except Exception as e:
            r = {"error": f"{type(e).__name__}: {e}"}
        recs.append(r)
        if r != oracle[key][i]:
            fails.append((i, precision, r, oracle[key][i]))
    report[key] = (len(recs) - len(fails), len(recs),
        vector_digest(recs) == VECTORS["groups"]["growth"] if key == "growth" else None,
        Counter(x[1] for x in fails), fails[:3])
recs, fails = [], []
for i, (targets, size, values) in enumerate(target_cases()):
    d = Draws(values)
    try:
        r = {"result": oai.choose_target(targets, d, board_size=size), "draws": list(d.used)}
    except Exception as e:
        r = {"error": f"{type(e).__name__}: {e}"}
    recs.append(r)
    if r != oracle["targets"][i]:
        fails.append((i, r, oracle["targets"][i]))
report["targets"] = (len(recs) - len(fails), len(recs), vector_digest(recs) == VECTORS["groups"]["targets"], fails[:3])
ok, fails = 0, []
for i, row in enumerate(SPECIAL_VECTORS):
    board = tuple(osp.Cell(model=m, arrows=a, owner=o, property=p, direction=dd) for m, a, o, p, dd in row["board"])
    hand = tuple(orules.Card(m, 10, 0, 10, 10, 1, a) for m, a in row["hand"])
    d = Draws(row["draws"])
    try:
        r = snapshot(osp.choose_special_move(hand, board, row["active"], d, oai.Personality(*row["personality"])))
        good = r == row["expected"] and d.used == row["used"]
    except Exception as e:
        good, r = False, repr(e)
    ok += good
    if not good: fails.append((i, len(row["board"]), r, row["expected"]))
report["special"] = (ok, len(SPECIAL_VECTORS), fails[:5])
for k, v in report.items(): print(k, v)
