"""Build first, then verify the wheel in a clean, dependency-free environment."""

import argparse
import json
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        required = {
            "tmserver/data/economy_catalog.json",
            "tmserver/data/profile_contract.json",
            "tmserver/data/lobby_commands.tsv",
            "tmserver/storage/migrations/env.py",
        }
        if not required.issubset(names):
            raise ValueError("wheel is missing runtime data or migrations")
        if not any(name.startswith("tmserver/storage/migrations/versions/") for name in names):
            raise ValueError("wheel is missing migration revisions")
        if any(name.split("/")[0] in {"tests", "research", "results", "tools"} for name in names):
            raise ValueError("development files leaked into the wheel")
    with tempfile.TemporaryDirectory(prefix="tm-wheel-") as folder:
        root = Path(folder)
        environment = root / "environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "TM_DATABASE_URL"}
        }

        def run(*command):
            return subprocess.run(
                command, cwd=root, env=env, check=True, capture_output=True, text=True, timeout=90
            ).stdout

        run(str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel))
        run(
            str(python), "-c",
            "import sys\n"
            "from tmserver.game.models import Card\n"
            "from tmserver.game.packets import packet, decode\n"
            "from tmserver.economy.packets import named, parse_named\n"
            "from tmserver.events.packets import event_list\n"
            "from tmserver.events.config import parse_event_configs\n"
            "from tmserver.rankings.models import RankingHistory\n"
            "from tmserver.rankings.periods import RankingPeriods\n"
            "from tmserver.rooms.packets import directory_files\n"
            "from tmserver.profiles.packets import answer, checked, checksum\n"
            "from tmserver.storage.base import StateStore\n"
            "card = Card(1, 50, 0, 40, 30, 1, 0)\n"
            "assert Card.parse(card.full()) == card\n"
            "assert decode(packet(0x43)).primary == 0x43\n"
            "assert parse_named(named(0xB0, 0, 1, '@Init=')) == (0xB0, 0, 1, {'Init': {}})\n"
            "assert len(event_list()) == 0x2C08\n"
            "assert set(directory_files()) == {'b/g/ZL', 'b/g/RL000'}\n"
            "assert len(answer()) == 24 and checksum(b'') == 0 and checked(b'') == bytes(4)\n"
            "assert RankingHistory().previous_ranks == (0, 0, 0, 0, 0)\n"
            "assert not set(sys.modules).intersection({\n"
            "    'tmserver.game.match', 'tmserver.economy.service', 'tmserver.events.service',\n"
            "    'tmserver.events.matchmaking', 'tmserver.events.matches',\n"
            "    'tmserver.rankings.service', 'tmserver.rooms.service', 'tmserver.profiles.service',\n"
            "    'tmserver.storage.json', 'tmserver.storage.postgres', 'sqlalchemy',\n"
            "})\n",
        )
        cli = scripts / ("tmserver.exe" if os.name == "nt" else "tmserver")
        run(str(cli), "--help")
        play_cli = scripts / ("tmserver-play.exe" if os.name == "nt" else "tmserver-play")
        run(str(play_cli), "--help")
        admin_cli = scripts / ("tmserver-admin.exe" if os.name == "nt" else "tmserver-admin")
        run(str(admin_cli), "--help")
        run(
            str(python), "-c",
            "from tmserver.economy.service import EconomyService\n"
            "from tmserver.storage.json import JsonStateStore\n"
            "economy = EconomyService(seed=1)\n"
            "economy.provision('wheel', nn=1, player_id=1, name=b'Wheel')\n"
            "JsonStateStore('admin.json').save(economy.snapshot())\n",
        )
        admin_state = root / "admin.json"
        original_accounts = json.loads(admin_state.read_text())["accounts"]
        assert "wheel" in run(str(admin_cli), "--state", str(admin_state), "accounts")
        run(
            str(admin_cli), "--state", str(admin_state), "prize-award",
            "--account-key", "wheel", "--id", "wheel-check", "--top30", "3",
        )
        awarded = json.loads(admin_state.read_text())
        assert awarded["accounts"] == original_accounts
        assert awarded["prize"]["accounts"]["wheel"]["pending"]["top30"] == 3
        before_summary = admin_state.read_bytes()
        summary = json.loads(run(
            str(admin_cli), "--state", str(admin_state), "account", "--account-key", "wheel"
        ))
        assert summary["pending_prize"]["id"] == "wheel-check"
        assert summary["prize_points"] == 0
        assert summary["card_count"] == 10
        assert admin_state.read_bytes() == before_summary
        recovery = json.loads(run(str(admin_cli), "--state", str(admin_state), "recovery"))
        assert recovery == dict(interrupted_rounds=0, unaccepted_results=0, accounts=[])
        assert admin_state.read_bytes() == before_summary
        accounts = run(str(cli), "--list-accounts")
        assert len(accounts.strip().splitlines()) == 2, accounts
        ready = run(
            str(python),
            "-B",
            "-m",
            "tmserver",
            "--game-port",
            "0",
            "--profile-port",
            "0",
            "--seconds",
            "0.1",
        )
        assert "services ready" in ready, ready
        assert (root / "var").is_dir(), "runtime output did not use the working directory"
        print(
            json.dumps(
                {
                    "wheel": wheel.name,
                    "files": len(names),
                    "clean_install": True,
                    "runtime_dependencies": [],
                    "console_entrypoint": True,
                    "module_entrypoint": True,
                    "json_admin_award": True,
                    "json_admin_account": True,
                    "json_recovery_preview": True,
                    "services_started_on_ephemeral_ports": True,
                }
            )
        )


if __name__ == "__main__":
    main()
