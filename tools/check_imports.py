"""Check model/codec isolation in a fresh interpreter, from source or an install."""

import argparse
import importlib
import json
from pathlib import Path
import sys
import sysconfig

FORBIDDEN = {
    "tmserver.game.match", "tmserver.events.matchmaking", "tmserver.events.matches",
    "tmserver.economy.auctions.ledger", "tmserver.network.server",
    "tmserver.storage.json", "tmserver.storage.postgres", "sqlalchemy", "alembic", "psycopg",
}


def check_loaded_modules(package_root, modules=None):
    modules = sys.modules if modules is None else modules
    unwanted = sorted(
        name for name in modules
        if name in FORBIDDEN or name.startswith("tmserver.") and name.endswith(".service")
    )
    if unwanted:
        raise ValueError(f"models/codecs loaded services or database dependencies: {unwanted}")
    outside = sorted(
        name for name, module in modules.items()
        if name.startswith("tmserver") and getattr(module, "__file__", None)
        and not Path(module.__file__).resolve().is_relative_to(package_root)
    )
    if outside:
        raise ValueError(f"runtime imports came from another package: {outside}")


def check(package_root):
    modules = sorted(
        ".".join(path.with_suffix("").relative_to(package_root.parent).parts)
        for path in package_root.rglob("*.py") if path.name in ("models.py", "packets.py")
    )
    if not modules:
        raise ValueError("no model/codec modules found")
    for name in modules + ["tmserver.events.config", "tmserver.rankings.periods", "tmserver.storage.validation"]:
        importlib.import_module(name)

    from tmserver.game.models import Card
    from tmserver.game.packets import packet, decode
    from tmserver.economy.packets import named, parse_named
    from tmserver.economy.auctions.models import Actor, Listing
    from tmserver.economy.auctions.packets import actor_record, listing_record
    from tmserver.events.packets import event_list
    from tmserver.rooms.packets import directory_files
    from tmserver.profiles.packets import answer, checked, checksum

    card = Card(1, 50, 0, 40, 30, 1, 0)
    assert Card.parse(card.full()) == card
    actor = Actor("wheel", 1, 1, b"Wheel", 0, 0, 0)
    listing = Listing(1, actor, card, 100, 20, 1, 0, 100, 3700, ())
    assert len(actor_record(actor)) == 0x38 and len(listing_record(listing)) == 0xC0
    assert decode(packet(0x43)).primary == 0x43
    assert parse_named(named(0xB0, 0, 1, "@Init=")) == (0xB0, 0, 1, {"Init": {}})
    assert len(event_list()) == 0x2C08
    assert set(directory_files()) == {"b/g/ZL", "b/g/RL000"}
    assert len(answer()) == 24 and checksum(b"") == 0 and checked(b"") == bytes(4)
    check_loaded_modules(package_root)
    return {"model_codec_modules": len(modules), "import_isolation": True, "provenance": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="src directory; omit to check installed code")
    args = parser.parse_args()
    if args.source_root is not None:
        sys.path.insert(0, str(args.source_root.resolve()))
    package_root = (
        (args.source_root or Path(sysconfig.get_path("purelib"))) / "tmserver"
    ).resolve(strict=True)
    print(json.dumps(check(package_root)))


if __name__ == "__main__":
    main()
