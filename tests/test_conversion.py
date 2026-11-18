"""Acceptance run for the converter: raster -> .ai, with no Adobe in the loop.

The check list is what a reviewer would actually open the file to verify:

 1. the .ai exists, is PDF-compatible and carries an AI private-data stream,
 2. VectorCraft can reopen it and reports an artboard matching the source,
 3. the same facts can be read straight out of the PDF layer (``inspect_ai``),
    so verification never needs an Adobe application,
 4. the rendered result matches the source raster, and the node count stayed sane,
 5. the preview and the SVG copy are real, not placeholders,
 6. a photograph goes through the photo profile without exploding,
 7. the shipping package contains no COM bridge at all, so no code path exists
    that could start Illustrator even by accident.

Run::

    python tests/test_conversion.py --output-root out/acceptance
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "shared"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import image2vector_core  # noqa: E402
from image2vector_core import inspect_ai, pipeline, vc  # noqa: E402

import make_fixture  # noqa: E402


def _ai_header(path: Path) -> dict:
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "kb": round(len(raw) / 1024, 1),
        "pdf": raw[:8].startswith(b"%PDF"),
        "private_data": b"AIPrivateData" in raw,
        "trailer_closed": b"%%EOF" in raw[-2048:],
    }


def _com_free() -> list[str]:
    """Every reference to a COM bridge inside the shipping package."""
    hits: list[str] = []
    for path in Path(image2vector_core.__file__).parent.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("win32com", "Illustrator.Application", "DoJavaScript"):
            if token in text:
                hits.append(f"{path.name}:{token}")
    return hits


def _build_photo(path: Path) -> Path:
    """A continuous-tone picture, so the photo profile actually gets used."""
    from PIL import Image

    width, height = 560, 420
    vertical = Image.linear_gradient("L").resize((width, height))
    horizontal = vertical.rotate(90, expand=True).resize((width, height))
    red = vertical.point(lambda value: 120 + value * 80 // 255)
    green = horizontal.point(lambda value: 110 + value * 70 // 255)
    blue = Image.blend(vertical, horizontal, 0.5).point(lambda value: 90 + value * 60 // 255)
    picture = Image.merge("RGB", (red, green, blue))
    grain = Image.effect_noise((width, height), 18).convert("RGB")
    Image.blend(picture, grain, 0.08).save(path, quality=88)
    return path


def _case(name: str, image: Path, work: Path, max_colors: int = 24) -> dict:
    folder = work / name
    folder.mkdir(parents=True, exist_ok=True)
    ai_path = folder / f"{name}.ai"
    png_path = folder / f"{name}-preview.png"
    svg_path = folder / f"{name}.svg"

    started = time.monotonic()
    report = pipeline.convert(
        image, ai_path,
        preview_path=png_path, svg_path=svg_path,
        job_root=folder / "job", job_name=name,
        max_colors=max_colors,
    )
    seconds = round(time.monotonic() - started, 2)

    if report.get("status") != "PASS":
        raise SystemExit(f"{name}: conversion failed -> {report.get('errors')}")
    trace = next((s for s in report["stages"] if s.get("stage") == "trace"), {})

    header = _ai_header(ai_path)
    structure = inspect_ai.inspect(ai_path)
    reopened = vc.inspect(ai_path)
    profile = vc.analyze(image)
    source_size = (profile["width"], profile["height"])
    artboard = reopened["artboards"][0]["rect"] if reopened["artboards"] else [0, 0, 0, 0]
    same_board = (round(artboard[2]), round(artboard[3])) == source_size

    svg_paths = 0
    if svg_path.exists():
        root = ET.parse(svg_path).getroot()
        svg_paths = sum(1 for _ in root.iter("{http://www.w3.org/2000/svg}path"))

    from PIL import Image

    with Image.open(image) as handle:
        pixel_area = handle.size[0] * handle.size[1]
    with Image.open(png_path) as handle:
        preview_size = handle.size

    checks = [
        {"name": f"{name}: status PASS", "ok": ai_path.exists()},
        {"name": f"{name}: .ai is PDF compatible", "ok": bool(header["pdf"])},
        {"name": f"{name}: .ai carries AI private data", "ok": bool(header["private_data"])},
        {"name": f"{name}: .ai trailer is closed", "ok": bool(header["trailer_closed"])},
        {"name": f"{name}: VectorCraft reopens it", "ok": int(reopened["objects"] or 0) > 0},
        {"name": f"{name}: PDF layer readable without Adobe",
         "ok": structure["reader"] == "pymupdf" and bool(structure["pdf_compatible"])},
        {"name": f"{name}: PDF layer holds real paths", "ok": int(structure["paths"] or 0) > 0},
        {"name": f"{name}: PDF artboard matches the source",
         "ok": (round(structure["width"] or 0), round(structure["height"] or 0)) == source_size},
        {"name": f"{name}: artboard matches the source", "ok": same_board},
        {"name": f"{name}: editable paths, not a bitmap",
         "ok": "Path" in (reopened["kinds"] or {})
               or "Compound Path" in (reopened["kinds"] or {})},
        {"name": f"{name}: preview is the source size", "ok": preview_size == source_size},
        {"name": f"{name}: SVG copy is real geometry", "ok": svg_paths > 0},
        {"name": f"{name}: node count stayed sane",
         "ok": (trace.get("paths") or 0) < max(400, pixel_area / 900)},
        {"name": f"{name}: close to the source (MAE < 4)",
         "ok": trace["fidelity"]["mae"] < 4.0},
        {"name": f"{name}: finished in under a minute", "ok": seconds < 60},
        {"name": f"{name}: stays under 20 MB", "ok": header["kb"] < 20_000},
    ]

    return {
        "name": name,
        "seconds": seconds,
        "checks": checks,
        "report": {key: trace.get(key) for key in
                   ("mode", "paths", "anchors", "colors", "objects",
                    "fidelity", "candidates", "trace_seconds")},
        "ai_kb": header["kb"],
        "artboard": artboard,
        "kinds": reopened["kinds"],
        "structure": structure,
        "source_size": source_size,
        "ai": str(ai_path),
        "preview": str(png_path),
        "svg": str(svg_path),
    }


def run(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)

    fixture = work / "figure.png"
    if not fixture.exists():
        make_fixture.build(fixture)
    photo = _build_photo(work / "photo.jpg")

    figure_case = _case("figure", fixture, work)
    photo_case = _case("photo", photo, work, max_colors=16)

    photo_checks = [
        {"name": "photo took the photo profile",
         "ok": photo_case["report"]["mode"] == "photo"},
        {"name": "photo kept colours in budget",
         "ok": int(photo_case["report"]["colors"] or 0) <= 64},
        {"name": "package has no COM bridge", "ok": not _com_free(),
         "note": ", ".join(_com_free()) or "no win32com / Illustrator.Application"},
    ]

    checks = figure_case["checks"] + photo_case["checks"] + photo_checks
    return {
        "status": "PASS" if all(item["ok"] for item in checks) else "FAIL",
        "checks": checks,
        "figure": figure_case,
        "photo": photo_case,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the raster -> .ai route")
    parser.add_argument("--output-root", type=Path, default=Path("out/acceptance"))
    args = parser.parse_args(argv)

    root = args.output_root.resolve()
    result = run(root)
    (root / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"status {result['status']}")
    for check in result["checks"]:
        note = f"  ({check['note']})" if check.get("note") else ""
        print(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['name']}{note}")
    for name in ("figure", "photo"):
        case = result[name]
        report = case["report"]
        print(f"  {name}: mode={report['mode']} paths={report['paths']} "
              f"colors={report['colors']} MAE={report['fidelity']['mae']} "
              f"{case['ai_kb']}KB {case['seconds']}s -> {case['ai']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
