"""Install the Codex and WorkBuddy editions from this checkout."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NEW = "image2vector"
OLD = "img2vector"


def _check_python(python: Path) -> None:
    if (not python.is_absolute() or not python.is_file()
            or (os.name == "nt" and python.suffix.lower() != ".exe")):
        raise ValueError("Supply an absolute path to a Python executable.")
    subprocess.run([
        str(python), "-I", "-B", "-c",
        "import sys; from PIL import __version__; "
        "sys.exit(0 if sys.version_info >= (3, 11) and __version__ == '13.0.0' else "
        "'Python 3.11+ with Pillow 13.0.0 is required.')",
    ], check=True)


def _new_python(run: Path) -> Path:
    env = run / "venv"
    subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(env)], check=True)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Use the install directory for pip's temporary data, including on Windows.
    pip_environment = {**os.environ, "TMPDIR": str(run), "TEMP": str(run), "TMP": str(run)}
    subprocess.run([
        sys.executable, "-I", "-B", "-m", "pip", "--isolated",
        "--python", str(env), "--disable-pip-version-check",
        "install", "--index-url", "https://pypi.org/simple", "--no-cache-dir",
        "--only-binary=:all:", "--no-deps", "--require-hashes", "-r",
        str(ROOT / "requirements.txt"),
    ], env=pip_environment, check=True)
    return python


def _regular_directory(path: Path) -> None:
    # Skill and backup moves must stay within their literal user directories.
    if path.resolve() != path or (os.path.lexists(path) and not path.is_dir()):
        raise ValueError(f"Expected a regular directory: {path}")


def _existing_skills(roots: list[Path]) -> list[Path]:
    found = []
    for root in roots:
        _regular_directory(root)
        for name in (OLD, NEW):
            installed = root / name
            if not os.path.lexists(installed):
                continue
            _regular_directory(installed)
            config_path = installed / "config.json"
            if config_path.is_symlink() or not config_path.is_file():
                raise ValueError(f"Unrecognized skill directory: {installed}")
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if (not isinstance(config, dict) or config.get("name") != name
                    or config.get("engine") != "vectorcraft-cli"):
                raise ValueError(f"Unrecognized skill directory: {installed}")
            found.append(installed)
    return found


def _move(source: Path, destination: Path) -> None:
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    source.rename(destination)


def install(editions: tuple[str, ...], profile: Path, python: Path | None = None) -> list[Path]:
    profile = profile.resolve()
    state = profile / ".image2vector"
    _regular_directory(state)
    roots_by_edition = {
        "codex": [profile / ".agents" / "skills", profile / ".codex" / "skills"],
        "workbuddy": [profile / ".workbuddy" / "skills"],
    }
    existing = {edition: _existing_skills(roots_by_edition[edition]) for edition in editions}
    destinations = {
        edition: (existing[edition][0].parent if existing[edition]
                  else roots_by_edition[edition][0]) / NEW
        for edition in editions
    }

    state.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="install-", dir=state))
    print(f"Install directory: {run}", flush=True)
    python = python or _new_python(run)
    _check_python(python)

    moves = []
    for edition in editions:
        package = run / edition
        shutil.copytree(ROOT / "shared", package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(ROOT / edition / "SKILL.md", package / "SKILL.md")
        shutil.copyfile(ROOT / "LICENSE", package / "LICENSE")
        (package / "python-path.txt").write_text(str(python) + "\n", encoding="utf-8")
        subprocess.run(
            [str(python), "-I", "-B", str(package / "scripts" / "image2vector.py"), "--help"],
            stdout=subprocess.DEVNULL, check=True,
        )
        for prior in existing[edition]:
            backup = run / "backups" / edition / prior.parent.parent.name / prior.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            moves.append((prior, backup))
        destination = destinations[edition]
        destination.parent.mkdir(parents=True, exist_ok=True)
        moves.append((package, destination))

    completed = []
    try:
        for source, destination in moves:
            _move(source, destination)
            completed.append((source, destination))
    except (OSError, KeyboardInterrupt):
        for source, destination in reversed(completed):
            _move(destination, source)
        raise
    if any(existing.values()):
        print(f"Previous skills saved in {run / 'backups'}")
    print(f"Python: {python}")
    return list(destinations.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["codex", "workbuddy", "both"], default="both")
    parser.add_argument("--python", type=Path, help="absolute path to an existing compatible Python")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required.")
    editions = ("codex", "workbuddy") if args.target == "both" else (args.target,)
    try:
        destinations = install(editions, Path.home(), args.python)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    for path in destinations:
        print(f"Installed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
