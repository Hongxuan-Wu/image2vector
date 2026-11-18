"""Trace an image in a unique job directory and deliver verified outputs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from . import vc


def _destination(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path.parent.resolve() / path.name


def _check_destinations(destinations: dict[str, Path]) -> None:
    paths = list(destinations.values())
    if len(set(paths)) != len(paths):
        raise ValueError("Output files must have distinct paths.")
    for path in paths:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise FileExistsError(f"Output already exists: {path}. Choose a new output name.")


def _deliver(produced: Path, wanted: Path) -> str:
    wanted.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem so readers see only the complete file.
    with tempfile.TemporaryDirectory(prefix=".image2vector-", dir=wanted.parent) as folder:
        staged = Path(folder) / "output"
        shutil.copyfile(produced, staged)
        if os.name == "nt":
            staged.rename(wanted)
        else:
            os.link(staged, wanted)
    return str(wanted)


def convert(source: str | Path, ai_path: str | Path, *,
            preview_path: str | Path | None = None,
            svg_path: str | Path | None = None,
            job_root: str | Path | None = None,
            job_name: str | None = None,
            max_colors: int = 24,
            mode: str = "auto",
            scale: float = 1.0,
            cli: str | Path | None = None,
            overrides: dict | None = None) -> dict:
    """Convert a raster to an AI and return the report, including any I/O error."""
    report: dict = {"status": "FAIL", "stages": []}
    job_dir = None
    delivered: dict[str, str] = {}
    try:
        image = Path(source).expanduser().absolute()
        target = _destination(ai_path)
        destinations = {}
        if preview_path is not None:
            destinations["preview"] = _destination(preview_path)
        if svg_path is not None:
            destinations["svg"] = _destination(svg_path)
        destinations["ai"] = target
        _check_destinations(destinations)

        root = Path(job_root).expanduser().resolve() if job_root else target.parent / ".ai-work"
        root.mkdir(parents=True, exist_ok=True)
        job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=root))
        report.update(job=job_name or target.stem, job_dir=str(job_dir))
        result = vc.trace(
            image, job_dir, preview=preview_path is not None, svg=svg_path is not None,
            max_colors=max_colors, mode=mode, scale=scale, cli=cli, overrides=overrides,
        )
        report["stages"].append({"stage": "trace", **result})

        _check_destinations(destinations)
        for key, destination in destinations.items():
            delivered[key] = _deliver(Path(result[key]), destination)
        report["outputs"] = {
            "output_ai": delivered["ai"],
            "output_png": delivered.get("preview"),
            "output_svg": delivered.get("svg"),
            **{key: result[key] for key in ("mode", "paths", "colors", "objects", "fidelity")},
        }
        report["status"] = "PASS"
    except (OSError, ValueError, vc.VectorCraftError) as error:
        report["errors"] = [str(error)]
        if delivered:
            report["partial_outputs"] = delivered

    if job_dir is not None:
        try:
            (job_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
        except OSError as error:
            report.setdefault("warnings", []).append(f"Could not write the job report: {error}")
    return report
