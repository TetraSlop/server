"""Run the documented baseline serially, with independent state for every case."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("results/load-baseline"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    backends = ["memory", "json"]
    if os.environ.get("TM_LOAD_DATABASE_URL"):
        backends.append("postgres")
    else:
        print("PostgreSQL omitted: set TM_LOAD_DATABASE_URL to include it.", flush=True)
    cases = [
        (backend, players, players)
        for backend in backends
        for players in ((32, 128) if backend == "memory" else (8, 32, 128))
    ]
    cases.extend((backend, 8, 1000) for backend in backends if backend != "memory")
    for backend, players, population in cases:
        name = f"{backend}-{players}-{population}"
        output = directory / f"{name}.json"
        print(f"Measuring {backend}: {players} clients, {population} accounts.", flush=True)
        with (directory / f"{name}.log").open("w", encoding="utf8") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tools.load_test",
                    "--backend",
                    backend,
                    "--players",
                    str(players),
                    "--population",
                    str(population),
                    "--seconds",
                    str(args.seconds),
                    "--output",
                    str(output),
                ],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        result = json.loads(output.read_text(encoding="utf8"))
        print(
            f"  {result['operations_per_second']} operations/s; "
            f"heartbeat p95 {result['latency']['heartbeat']['p95_ms']} ms; "
            f"{len(result['failures'])} failures.",
            flush=True,
        )


if __name__ == "__main__":
    main()
