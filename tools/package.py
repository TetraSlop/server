"""The intended package files, shared by clean builds and wheel verification."""

from collections import Counter
from pathlib import Path
import shutil
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def runtime_manifest(source=ROOT):
    source = Path(source)
    package = source / "src" / "tmserver"
    config = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    files = set(package.rglob("*.py"))
    for pattern in config["tool"]["setuptools"]["package-data"]["tmserver"]:
        files.update(package.glob(pattern))
    return {
        path.relative_to(source / "src").as_posix(): path.read_bytes()
        for path in sorted(files)
        if path.is_file() and "__pycache__" not in path.parts
    }


def verify_wheel(wheel, source=ROOT):
    expected = runtime_manifest(source)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate wheel entries: {duplicates}")
        files = {name for name in names if not name.endswith("/")}
        actual = {name for name in files if name.startswith("tmserver/")}
        extra, missing = sorted(actual - expected.keys()), sorted(expected.keys() - actual)
        if extra or missing:
            raise ValueError(f"wheel/source manifest differs: extra={extra}, missing={missing}")
        changed = sorted(name for name, data in expected.items() if archive.read(name) != data)
        if changed:
            raise ValueError(f"wheel/source bytes differ: {changed}")
        unexpected = sorted(
            name for name in files - actual
            if len(Path(name).parts) < 2 or not Path(name).parts[0].endswith(".dist-info")
        )
        if unexpected:
            raise ValueError(f"unexpected files outside the package: {unexpected}")
    return {"files": len(names), "runtime_files": len(expected), "exact_source": True}


def stage_source(destination, source=ROOT):
    """Populate a new staging directory without copying caches or live state."""
    destination, source = Path(destination), Path(source)
    destination.mkdir()  # Refuse to reuse an existing build tree.
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(source / name, destination / name)
    for name, data in runtime_manifest(source).items():
        target = destination / "src" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
