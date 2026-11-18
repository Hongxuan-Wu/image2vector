"""Convert a raster file to editable Illustrator artwork."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image2vector_core import pipeline  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="image2vector",
        description="Turn a PNG, JPEG, TIFF, BMP or WebP into an editable .ai file.",
    )
    parser.add_argument("image", type=Path, help="source raster image")
    parser.add_argument("-o", "--output", type=Path, help="new .ai path")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="artboard scale, 1.0 = one source pixel per point")
    parser.add_argument("--max-colors", type=int, default=24, metavar="4..64",
                        help="colour budget for photo-like input")
    parser.add_argument("--mode", default="auto", choices=["auto", "flat", "photo", "bw"],
                        help="tracing profile")
    parser.add_argument("--vc-cli", help="absolute path to the trusted VectorCraft executable")
    parser.add_argument("--no-preview", action="store_true", help="skip the PNG preview")
    parser.add_argument("--no-svg", action="store_true", help="skip the SVG copy")
    parser.add_argument("--work-dir", type=Path, help="job root (default: alongside the .ai)")
    args = parser.parse_args(argv)

    source = args.image.expanduser()
    target = args.output.expanduser() if args.output else source.with_suffix(".ai")
    if target.suffix.lower() != ".ai":
        target = target.with_suffix(".ai")
    report = pipeline.convert(
        source, target,
        preview_path=None if args.no_preview else target.with_name(target.stem + "-preview.png"),
        svg_path=None if args.no_svg else target.with_suffix(".svg"),
        job_root=args.work_dir,
        max_colors=args.max_colors,
        mode=args.mode,
        scale=args.scale,
        cli=args.vc_cli,
    )

    trace = next((stage for stage in report["stages"] if stage["stage"] == "trace"), {})
    outputs = report.get("outputs", {})
    ok = report["status"] == "PASS"
    print(json.dumps({
        "ok": ok,
        "source": str(source),
        "ai": outputs.get("output_ai"),
        "ai_kb": trace.get("ai_kb", 0) if ok else 0,
        "preview": outputs.get("output_png"),
        "svg": outputs.get("output_svg"),
        "mode": trace.get("mode"),
        "paths": trace.get("paths"),
        "colors": trace.get("colors"),
        "objects": trace.get("objects"),
        "fidelity": trace.get("fidelity"),
        "candidates": trace.get("candidates"),
        "seconds": trace.get("seconds"),
        "cli_version": trace.get("cli_version"),
        "job_dir": report.get("job_dir"),
        "warnings": trace.get("warnings", []) + report.get("warnings", []),
        "errors": report.get("errors", []),
        "partial_outputs": report.get("partial_outputs", {}),
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
