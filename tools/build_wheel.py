"""Build a wheel from fresh temporary sources, then verify it against this checkout."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from tools.package import ROOT, stage_source, verify_wheel


def build(output, source=ROOT):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tm-build-") as directory:
        stage = Path(directory) / "source"
        stage_source(stage, source)
        wheels = Path(directory) / "wheels"
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels), str(stage)],
            check=True,
        )
        built = list(wheels.glob("*.whl"))
        if len(built) != 1:
            raise ValueError("expected exactly one built wheel")
        verify_wheel(built[0], source)
        target = output / built[0].name
        target.write_bytes(built[0].read_bytes())
        return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.outdir))


if __name__ == "__main__":
    main()
