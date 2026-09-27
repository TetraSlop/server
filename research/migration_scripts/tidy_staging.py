"""One-time normalization of the extracted transaction methods."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/tmserver"
NAMES = (
    "auction_economy",
    "auction_service",
    "prize_service",
    "event_service",
    "event_cards",
    "event_results",
    "match_economy",
    "result_rewards",
    "rating_policy",
    "character_profiles",
)

for name in NAMES:
    path = PACKAGE / (name + ".py")
    source = (
        path.read_text()
        .replace("    def stage(", "    def stage_state(", 1)
        .replace("    def publish(", "    def publish_state(", 1)
    )
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    removed = set()
    additions = {}
    copied_imports = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "copy":
            copied_imports.update(alias.name for alias in node.names)
            removed.update(range(node.lineno - 1, node.end_lineno))
        if isinstance(node, ast.ClassDef):
            initializer = next(
                (n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"),
                None,
            )
            moved = [
                n
                for n in node.body
                if isinstance(n, ast.FunctionDef)
                and n.name in ("stage_state", "publish_state", "stage_returns", "publish_returns")
            ]
            if initializer and moved:
                body = []
                for method in moved:
                    body.extend(lines[method.lineno - 1 : method.end_lineno])
                    body.append("\n")
                    removed.update(range(method.lineno - 1, method.end_lineno))
                additions[initializer.end_lineno - 1] = "\n" + "".join(body)
    additions[tree.body[0].end_lineno - 1] = (
        "\nfrom copy import " + ", ".join(sorted(copied_imports)) + "\n"
    )
    result = "".join(
        (line if i not in removed else "") + additions.get(i, "") for i, line in enumerate(lines)
    )
    path.write_text(result, encoding="utf8", newline="\n")

for name in ("claim_transactions", "match_transactions", "profile_transactions"):
    path = PACKAGE / (name + ".py")
    source = path.read_text()
    for service in (
        "auction",
        "auction_ledger",
        "prize",
        "events",
        "event_cards",
        "event_results",
        "match_economy",
        "result_rewards",
        "ratings",
        "character_profiles",
    ):
        source = source.replace(f"host.{service}.stage(", f"host.{service}.stage_state(").replace(
            f"host.{service}.publish(", f"host.{service}.publish_state("
        )
    path.write_text(source, encoding="utf8", newline="\n")

path = PACKAGE / "economy_service.py"
source = (
    path.read_text()
    .replace(
        "from dataclasses import dataclass, field, replace",
        "from dataclasses import dataclass, replace\nfrom dataclasses import field as dataclass_field",
    )
    .replace("= field(", "= dataclass_field(")
)
path.write_text(source, encoding="utf8", newline="\n")
path = ROOT / "tests/test_native_departure_rules.py"
path.write_text(
    path.read_text().replace(
        "from tmserver.native_departure_rules import *",
        "from tmserver.native_departure_rules import DepartureError, next_turn, result_slots",
    ),
    encoding="utf8",
    newline="\n",
)
