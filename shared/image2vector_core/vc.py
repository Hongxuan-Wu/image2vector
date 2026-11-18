"""VectorCraft backend: raster image -> editable ``.ai``.

VectorCraft (https://getartcraft.com/apps/vectorcraft) ships a headless CLI with
the same command catalogue its GUI uses, so the whole job -- trace the bitmap,
expand the trace into real paths, save a PDF-compatible ``.ai`` -- runs in one
subprocess.  No Adobe application, no COM bridge, no JSX.

The only judgement this module adds on top of the CLI is *which* tracing
parameters suit the picture: a flat figure (chart, diagram, schematic) wants a
fidelity preset with `automatic` colours, while a photograph wants a limited
palette.  Everything else is VectorCraft's own geometry.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections import Counter
from pathlib import Path

from . import inspect_ai

CLI_NAME = "vectorcraft-cli.exe" if os.name == "nt" else "vectorcraft-cli"

#: Default install roots used by the vendor's launcher and installer.
#: ``VECTORCRAFT_CLI`` wins over all of them.
CLI_CANDIDATES = (
    Path(r"C:\Program Files\ArtCraft\VectorCraft") / CLI_NAME,
    Path(r"C:\Program Files\VectorCraft") / CLI_NAME,
)

CLI_GLOBS = (
    r"C:\Program Files\ArtCraft\*\vectorcraft-cli.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\ArtCraft\*\vectorcraft-cli.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\VectorCraft\vectorcraft-cli.exe"),
)

RASTER_FORMATS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".tif": "TIFF",
    ".tiff": "TIFF", ".bmp": "BMP", ".webp": "WEBP",
}
MAX_BYTES = 128 * 1024 * 1024
MAX_EDGE = 16_384
MAX_PIXELS = 32_000_000


class VectorCraftError(RuntimeError):
    """The CLI is missing, or it refused a step."""


# --------------------------------------------------------------------------- #
# locating the CLI
# --------------------------------------------------------------------------- #
def _local_hints():
    for directory in ("image2vector", "img2vector"):
        hint = Path.home() / ".config" / directory / "vectorcraft-cli"
        if hint.is_file():
            value = hint.read_text(encoding="utf-8-sig").strip()
            if value:
                yield value


def _executable(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or (os.name == "nt" and path.drive.startswith("\\\\")):
        raise VectorCraftError(f"VectorCraft requires an absolute local executable path: {path}")
    path = path.resolve()
    if (not path.is_file()
            or (os.name == "nt" and (path.suffix.lower() != ".exe" or path.drive.startswith("\\\\")))
            or (os.name != "nt" and not os.access(path, os.X_OK))):
        raise VectorCraftError(f"Invalid VectorCraft executable: {path}")
    return path


def find_cli(explicit: str | Path | None = None) -> Path:
    """Return the ``vectorcraft-cli`` executable, or explain where to look."""
    configured = explicit or os.environ.get("VECTORCRAFT_CLI")
    if configured:
        return _executable(configured)
    for hinted in _local_hints():
        return _executable(hinted)

    for path in CLI_CANDIDATES:
        if path.is_absolute() and path.is_file():
            return _executable(path)

    import glob

    for pattern in CLI_GLOBS:
        for hit in sorted(glob.glob(pattern), reverse=True):
            if Path(hit).is_absolute() and Path(hit).is_file():
                return _executable(hit)

    # Windows' executable search can consult the current directory before PATH.
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(entry.strip('"'))
        if not directory.is_absolute() or (os.name == "nt" and directory.drive.startswith("\\\\")):
            continue
        path = directory / CLI_NAME
        if path.is_file() and os.access(path, os.X_OK):
            return _executable(path)

    raise VectorCraftError(
        "could not find vectorcraft-cli. Install VectorCraft "
        "(getartcraft.com/apps/vectorcraft) or point VECTORCRAFT_CLI at the "
        "executable; the launcher keeps it in the same folder as "
        "vectorcraft.exe."
    )


def _invoke(cli: Path, args: list[str], timeout: float) -> str:
    environment = os.environ.copy()
    environment["VECTORCRAFT_NO_PREFS"] = "1"
    try:
        completed = subprocess.run(
            [str(cli), "--safe-mode", *args],
            cwd=cli.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise VectorCraftError(f"VectorCraft {args[0]} exceeded {timeout:g} seconds") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        tail = " / ".join(detail[-4:]) if detail else f"exit {completed.returncode}"
        raise VectorCraftError(f"vectorcraft-cli {args[0]} failed: {tail}")
    return completed.stdout


def _run(cli: Path, args: list[str], timeout: float) -> list[dict]:
    """Run a batch and return the structured output for each step."""
    steps: list[dict] = []
    for line in _invoke(cli, args, timeout).splitlines():
        try:
            step = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(step, dict):
            continue
        if step.get("error"):
            raise VectorCraftError(f"step {step.get('step')}: {step['error']}")
        steps.append(step)
    return steps


def version(cli: Path | None = None) -> str:
    """Version as the installer recorded it, or ``unknown`` off Windows."""
    find_cli(cli)
    try:
        import winreg
    except ImportError:  # not on Windows: the CLI itself has no --version
        return "unknown"

    roots = [r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"]
    for root in roots:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, name) as entry:
                            display = winreg.QueryValueEx(entry, "DisplayName")[0]
                            if display.strip().lower() == "vectorcraft":
                                return str(winreg.QueryValueEx(entry, "DisplayVersion")[0])
                    except OSError:
                        continue
        except OSError:
            continue
    return "unknown"


def _check_size(width: float, height: float) -> None:
    if not (1 <= width <= MAX_EDGE and 1 <= height <= MAX_EDGE
            and width * height <= MAX_PIXELS):
        raise VectorCraftError(f"Image or artboard exceeds size limits: {width:g} × {height:g}")


def _raster_to_png(source: Path, target: Path) -> None:
    from PIL import Image

    expected = RASTER_FORMATS.get(source.suffix.lower())
    if not expected:
        raise VectorCraftError("Expected a PNG, JPEG, TIFF, BMP or WebP input.")
    if not source.is_file():
        raise VectorCraftError(f"No such raster file: {source}")
    try:
        with source.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > MAX_BYTES:
                raise VectorCraftError("Input exceeds the 128 MiB file size limit.")
            with Image.open(stream, formats=[expected]) as handle:
                _check_size(*handle.size)
                alpha = "A" in handle.getbands() or "transparency" in handle.info
                image = handle.convert("RGBA" if alpha else "RGB")
    except (OSError, Image.DecompressionBombError) as error:
        raise VectorCraftError(f"Cannot decode {expected} input: {error}") from error
    # Give the native tracer only decoded pixels and discard input metadata.
    image.info.clear()
    image.save(target, format="PNG")


# --------------------------------------------------------------------------- #
# reading the picture
# --------------------------------------------------------------------------- #
def analyze(image: Path) -> dict:
    """Describe the picture well enough to pick tracing parameters.

    ``flat``   a drawn figure: few colours, large solid areas
    ``photo``  continuous tone
    ``border_white`` the outer frame is white, so the background can be dropped
    """
    from PIL import Image

    with Image.open(image, formats=[RASTER_FORMATS[image.suffix.lower()]]) as handle:
        width, height = handle.size
        has_alpha = handle.mode in ("RGBA", "LA") or (
            handle.mode == "P" and "transparency" in handle.info
        )
        rgb = handle.convert("RGB")

    scale = min(1.0, 320.0 / max(width, height, 1))
    if scale < 1.0:
        rgb = rgb.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                         Image.Resampling.BILINEAR)
    pixels = list(rgb.get_flattened_data())
    total = len(pixels) or 1

    buckets = Counter(((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3) for r, g, b in pixels)
    top_coverage = sum(count for _, count in buckets.most_common(32)) / total
    distinct = len(buckets)

    saturated = sum(1 for r, g, b in pixels[::7] if max(r, g, b) - min(r, g, b) > 24)
    saturation = saturated / max(1, len(pixels[::7]))

    small = rgb
    w, h = small.size
    edge = max(1, min(w, h) // 20)
    strips = [
        small.crop((0, 0, w, edge)), small.crop((0, h - edge, w, h)),
        small.crop((0, 0, edge, h)), small.crop((w - edge, 0, w, h)),
    ]
    border_pixels = [px for strip in strips for px in strip.get_flattened_data()]
    white_share = sum(1 for r, g, b in border_pixels if min(r, g, b) >= 245) / max(1, len(border_pixels))

    flat = distinct <= 48 and top_coverage >= 0.94
    if not flat and distinct <= 160 and top_coverage >= 0.985:
        flat = True
    kind = "flat" if flat else "photo"

    return {
        "width": width,
        "height": height,
        "distinct_colors": distinct,
        "top32_coverage": round(top_coverage, 4),
        "saturation": round(saturation, 3),
        "border_white": round(white_share, 3),
        "has_alpha": has_alpha,
        "kind": kind,
        "megapixels": round(width * height / 1e6, 2),
    }


def params_for(profile: dict, max_colors: int = 24, mode: str = "auto",
               ignore_white: bool | None = None, overrides: dict | None = None) -> dict:
    """Return the ``imageTrace.makeAndExpand`` parameters for this picture."""
    kind = "flat" if mode == "auto" else mode
    drop_white = (profile["border_white"] >= 0.95) if ignore_white is None else ignore_white

    if kind in ("photo", "photo-heavy"):
        params = {
            "mode": "color",
            "palette": "limited",
            "colors": max(4, min(64, int(max_colors))),
            "paths": 45,
            "corners": 35,
            "noise": 8,          # absorb JPEG ringing before it becomes specks
            "method": "abutting",
            "fills": True,
            "ignoreWhite": False,
        }
    elif kind in ("bw", "blackwhite", "line"):
        params = {
            "mode": "blackAndWhite",
            "threshold": 128,
            "paths": 85,
            "corners": 65,
            "noise": 2,
            "fills": True,
            "ignoreWhite": drop_white,
        }
    else:  # flat figure
        params = {
            "mode": "color",
            "palette": "automatic",
            "paths": 85,
            "corners": 60,
            "noise": 2,
            "method": "abutting",
            "fills": True,
            "ignoreWhite": drop_white,
        }
    if overrides:
        params.update({k: v for k, v in overrides.items() if v is not None})
    return params


# --------------------------------------------------------------------------- #
# scoring a candidate against the source
# --------------------------------------------------------------------------- #
def _as_rgb_white(path: Path):
    """Open an image, compositing any alpha onto white."""
    from PIL import Image

    with Image.open(path, formats=["PNG"]) as handle:
        _check_size(*handle.size)
        if handle.mode in ("RGBA", "LA", "P"):
            frame = handle.convert("RGBA")
            canvas = Image.new("RGBA", frame.size, (255, 255, 255, 255))
            canvas.alpha_composite(frame)
            return canvas.convert("RGB")
        return handle.convert("RGB")


def score(source: Path, rendered: Path) -> dict:
    """Mean absolute error between the source and a rendered candidate."""
    from PIL import ImageChops, ImageStat

    a = _as_rgb_white(source)
    b = _as_rgb_white(rendered)
    if a.size != b.size:
        from PIL import Image
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a, b)
    channels = ImageStat.Stat(diff).mean
    mae = sum(channels) / len(channels)
    mask = diff.convert("L").point(lambda value: 255 if value > 24 else 0)
    deviated = ImageStat.Stat(mask).mean[0] / 255 * 100
    return {"mae": round(mae, 2), "deviated_pct": round(deviated, 2)}



# --------------------------------------------------------------------------- #
# tracing
# --------------------------------------------------------------------------- #
def _auto_modes(profile: dict) -> list[str]:
    """Candidate profiles to try when the caller did not name one."""
    if profile["megapixels"] > 3.0:
        return ["photo"]
    modes = ["flat", "photo"]
    if profile["saturation"] < 0.02 and profile["distinct_colors"] <= 24:
        modes = ["bw", "flat"]
    return modes


def _attempt(executable: Path, source: Path, ai_path: Path, png_path: Path,
             params: dict, by_color: bool, scale: float, profile: dict,
             timeout: float, steps_file: Path, svg_path: Path | None = None) -> dict:
    steps: list[dict] = [
        {"command": "select.all", "params": {}},
        {"command": "imageTrace.makeAndExpand",
         "params": {"params": params, "byColor": bool(by_color)}},
    ]
    if scale != 1.0:
        steps.append({"command": "select.all", "params": {}})
        steps.append({"command": "object.transform",
                      "params": {"matrix": [scale, 0, 0, scale, 0, 0]}})
        steps.append({"command": "artboard.setProps",
                      "params": {"index": 0, "x": 0, "y": 0,
                                 "width": round(profile["width"] * scale, 3),
                                 "height": round(profile["height"] * scale, 3)}})
    steps_file.write_text(json.dumps(steps), encoding="utf-8")

    started = time.monotonic()
    exports = ["--export", str(ai_path), "--export", str(png_path)]
    if svg_path is not None:
        exports += ["--export", str(svg_path)]
    output = _run(executable, ["run", "--in", str(source), "--commands", str(steps_file), *exports],
                  timeout=timeout)
    elapsed = time.monotonic() - started

    trace_step = next((s for s in output if s.get("command") == "imageTrace.makeAndExpand"), {})
    result = trace_step.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("paths"), int) or result["paths"] < 1:
        raise VectorCraftError("VectorCraft returned no traced paths.")
    if not ai_path.is_file() or ai_path.stat().st_size == 0:
        raise VectorCraftError(f"VectorCraft wrote no .ai at {ai_path}")
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise VectorCraftError(f"VectorCraft wrote no PNG at {png_path}")
    if svg_path is not None and (not svg_path.is_file() or svg_path.stat().st_size == 0):
        raise VectorCraftError(f"VectorCraft wrote no SVG at {svg_path}")
    return {
        "ai": ai_path,
        "png": png_path,
        "svg": svg_path,
        "paths": result.get("paths"),
        "anchors": result.get("anchors"),
        "colors": result.get("colors"),
        "seconds": round(elapsed, 2),
        "warnings": list(result.get("warnings") or []),
    }


def trace(source: Path, scratch: Path, *, preview: bool, svg: bool,
          max_colors: int = 24, mode: str = "auto", scale: float = 1.0,
          overrides: dict | None = None,
          by_color: bool = True, cli: str | Path | None = None,
          timeout: float = 120.0) -> dict:
    """Trace a decoded PNG in the caller's newly created job directory."""
    if mode not in {"auto", "flat", "photo", "bw"}:
        raise ValueError(f"Unknown tracing mode: {mode}")
    if not 4 <= max_colors <= 64:
        raise ValueError("Colour budget must be between 4 and 64.")
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Artboard scale must be finite and positive.")

    bitmap = scratch / "source.png"
    _raster_to_png(source, bitmap)
    executable = find_cli(cli)
    profile = analyze(bitmap)
    _check_size(profile["width"] * scale, profile["height"] * scale)

    modes = _auto_modes(profile) if mode == "auto" else [mode]
    attempts: list[dict] = []
    for index, kind in enumerate(modes):
        params = params_for(profile, max_colors=max_colors, mode=kind,
                            overrides=overrides)
        candidate = scratch / f"candidate-{index}-{kind}"
        attempt = _attempt(
            executable, bitmap, candidate.with_suffix(".ai"), candidate.with_suffix(".png"),
            params, by_color, scale, profile, timeout, candidate.with_suffix(".json"),
            svg_path=candidate.with_suffix(".svg") if svg else None,
        )
        attempt["mode"] = kind
        attempt["params"] = params
        attempt["score"] = score(bitmap, attempt["png"])
        # A flat-profile trace of a photograph explodes into hundreds of
        # colours: keep it only if nothing else wins.
        attempt["over_colored"] = bool(
            attempt["colors"] and attempt["colors"] > 4 * max(4, max_colors))
        attempts.append(attempt)

    usable = [a for a in attempts if not a["over_colored"]] or attempts
    best = min(usable, key=lambda a: (a["score"]["mae"], a["paths"] or 10 ** 9))

    artwork = best["ai"]
    header = inspect_ai.pdf_header(artwork)
    if not all(header[key] for key in ("pdf_compatible", "private_data", "trailer_closed")):
        raise VectorCraftError("Export is missing the PDF-compatible Illustrator structure.")
    reopened = inspect(artwork, cli=executable, timeout=timeout)
    kinds = reopened["kinds"]
    if not (kinds.get("Path") or kinds.get("Compound Path")) or kinds.get("Image"):
        raise VectorCraftError("The reopened .ai did not contain vector-only path artwork.")

    report_preview = None
    if preview:
        report_preview = scratch / "preview.png"
        _as_rgb_white(best["png"]).save(report_preview, format="PNG")

    return {
        "backend": "vectorcraft",
        "cli": str(executable),
        "cli_version": version(executable),
        "source": str(source),
        "ai": str(artwork),
        "ai_kb": header["kb"],
        "preview": str(report_preview) if report_preview is not None else None,
        "svg": str(best["svg"]) if best["svg"] is not None else None,
        "paths": best["paths"],
        "anchors": best["anchors"],
        "colors": best["colors"],
        "seconds": round(sum(a["seconds"] for a in attempts), 2),
        "mode": best["mode"],
        "profile": profile,
        "params": best["params"],
        "by_color": bool(by_color),
        "fidelity": best["score"],
        "candidates": [
            {"mode": a["mode"], "paths": a["paths"], "colors": a["colors"],
             **a["score"], "seconds": a["seconds"]}
            for a in attempts
        ],
        "objects": reopened["objects"],
        "warnings": list(dict.fromkeys([*best["warnings"], *reopened["warnings"]])),
    }


# --------------------------------------------------------------------------- #
# reading a finished .ai back
# --------------------------------------------------------------------------- #
def inspect(ai_path: str | Path, cli: str | Path | None = None,
            timeout: float = 300.0) -> dict:
    """Reopen the ``.ai`` with VectorCraft and count what is inside it."""
    path = Path(ai_path).expanduser().resolve()
    if not path.is_file():
        raise VectorCraftError(f"no such file: {path}")
    executable = find_cli(cli)
    payload = json.loads(_invoke(executable, ["info", str(path)], timeout))
    if not isinstance(payload, dict):
        raise VectorCraftError("Malformed VectorCraft info response.")
    info = payload.get("info") or {}
    return {
        "file": str(path),
        "objects": info.get("objects"),
        "kinds": payload.get("kinds") or {},
        "artboards": payload.get("artboards") or [],
        "fonts": payload.get("fonts") or [],
        "warnings": payload.get("warnings") or [],
        "color_mode": info.get("colorMode"),
    }


__all__ = [
    "VectorCraftError", "find_cli", "analyze", "params_for", "trace",
    "inspect", "version",
]
