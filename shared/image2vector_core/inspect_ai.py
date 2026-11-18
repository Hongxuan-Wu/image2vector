"""Read a PDF-compatible .ai back **without** opening Illustrator.

The old acceptance route asked Adobe's COM bridge what it saw, which had two
side effects we do not want in a converter: it started Illustrator (and left it
running), and it made the whole pipeline look Adobe-dependent.

Everything we actually need is already in the file, because a PDF-compatible
.ai *is* a PDF: page geometry, path count, fill/stroke colours, embedded
bitmaps and the private-data marker. This module reads those with PyMuPDF and
plain byte scanning, so verification is offline and instant.
"""

from __future__ import annotations

import json
from pathlib import Path

PRIVATE_MARKER = b"AIPrivateData"


def _contains(path: Path, marker: bytes, chunk: int = 1 << 20) -> bool:
    """Byte-scan a possibly large file without loading all of it."""
    with path.open("rb") as handle:
        carry = b""
        while True:
            block = handle.read(chunk)
            if not block:
                return False
            if marker in carry + block:
                return True
            carry = block[-len(marker):]


def pdf_header(path: Path) -> dict:
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(8)
        handle.seek(max(0, size - 4096))
        trailer_closed = b"%%EOF" in handle.read()
    return {
        "file": str(path),
        "bytes": size,
        "kb": round(size / 1024, 1),
        "pdf_compatible": head.startswith(b"%PDF-"),
        "pdf_version": head[1:8].decode("ascii", "replace") if head.startswith(b"%PDF-") else None,
        "private_data": _contains(path, PRIVATE_MARKER),
        "trailer_closed": trailer_closed,
    }


def inspect(ai_path: str | Path) -> dict:
    """Structural summary of a .ai file."""
    path = Path(ai_path).expanduser().resolve()
    report = {
        **pdf_header(path),
        "pages": None,
        "artboard": None,
        "width": None,
        "height": None,
        "paths": None,
        "segments": None,
        "fill_colors": None,
        "stroke_colors": None,
        "images": [],
        "text_runs": None,
        "reader": "none",
        "notes": [],
    }

    try:
        import pymupdf
    except ImportError:  # pragma: no cover - optional dependency
        report["notes"].append("pymupdf not installed: geometry checks skipped")
        return report

    try:
        doc = pymupdf.open(str(path))
    except Exception as error:  # noqa: BLE001 - corrupt or non-PDF .ai
        report["notes"].append(f"pymupdf could not open it: {error}")
        return report

    try:
        report["reader"] = "pymupdf"
        report["pages"] = doc.page_count
        page = doc[0]
        rect = page.rect
        report["artboard"] = [round(rect.x0, 2), round(rect.y0, 2),
                              round(rect.x1, 2), round(rect.y1, 2)]
        report["width"] = round(rect.width, 2)
        report["height"] = round(rect.height, 2)

        fills: set[tuple] = set()
        strokes: set[tuple] = set()
        drawings = page.get_drawings()
        segments = 0
        for item in drawings:
            segments += len(item.get("items") or [])
            fill = item.get("fill")
            if fill is not None:
                fills.add(tuple(round(v, 3) for v in fill))
            color = item.get("color")
            if color is not None:
                strokes.add(tuple(round(v, 3) for v in color))

        report["paths"] = len(drawings)
        report["segments"] = segments
        report["fill_colors"] = len(fills)
        report["stroke_colors"] = len(strokes)

        images = []
        for entry in page.get_images(full=True):
            images.append({"xref": entry[0], "width": entry[2], "height": entry[3],
                           "bpc": entry[4], "colorspace": entry[5]})
        report["images"] = images

        text = page.get_text("dict")
        runs = sum(len(block.get("lines", [])) for block in text.get("blocks", [])
                   if block.get("type") == 0)
        report["text_runs"] = runs
    finally:
        doc.close()

    return report


def summary_line(report: dict) -> str:
    board = report.get("artboard") or [0, 0, 0, 0]
    return (f"{report['kb']} KB | artboard {board[2]:.0f}x{board[3]:.0f} pt | "
            f"paths {report['paths']} | fills {report['fill_colors']} | "
            f"images {len(report['images'])} | text runs {report['text_runs']}")


if __name__ == "__main__":
    import sys

    print(json.dumps(inspect(sys.argv[1]), ensure_ascii=False, indent=2))
