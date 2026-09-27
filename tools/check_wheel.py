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
            "tmserver/migrations/env.py",
        }
        if not required.issubset(names):
            raise ValueError("wheel is missing runtime data or migrations")
        if not any(name.startswith("tmserver/migrations/versions/") for name in names):
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
        cli = scripts / ("tmserver.exe" if os.name == "nt" else "tmserver")
        run(str(cli), "--help")
        play_cli = scripts / ("tmserver-play.exe" if os.name == "nt" else "tmserver-play")
        run(str(play_cli), "--help")
        admin_cli = scripts / ("tmserver-admin.exe" if os.name == "nt" else "tmserver-admin")
        run(str(admin_cli), "--help")
        run(
            str(python), "-c",
            "from tmserver.economy_service import EconomyService\n"
            "from tmserver.state_store import JsonStateStore\n"
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
                    "services_started_on_ephemeral_ports": True,
                }
            )
        )


if __name__ == "__main__":
    main()
