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
