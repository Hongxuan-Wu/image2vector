"""Report what a .ai file contains, without starting Illustrator.

The .ai this tool writes is PDF-compatible, so its geometry, colours and
embedded bitmaps can be read straight from the PDF layer. That keeps
verification offline: no Adobe account, no COM bridge, no window popping up.

    python verify_ai_pdf.py figure.ai          # JSON summary
    python verify_ai_pdf.py figure.ai --line   # one-line summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "shared"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image2vector_core import inspect_ai  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a .ai file via its PDF layer")
    parser.add_argument("ai", type=Path, help="path to the .ai file")
    parser.add_argument("--line", action="store_true", help="print a single summary line")
    args = parser.parse_args(argv)

    path = args.ai.expanduser()
    if not path.is_file():
        print(json.dumps({"ok": False, "error": f"no such file: {path}"}, ensure_ascii=False))
        return 2

    try:
        report = inspect_ai.inspect(path)
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"},
                         ensure_ascii=False))
        return 1

    report["ok"] = bool(
        report["pdf_compatible"] and report["private_data"] and report["trailer_closed"]
        and (report["paths"] or 0) > 0 and not report["images"]
    )
    if args.line:
        print(inspect_ai.summary_line(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
