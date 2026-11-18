"""Automatic-selection regressions with simulated engine replies, no CLI required."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "tests")]

from image2vector_core import pipeline, vc  # noqa: E402
import make_fixture  # noqa: E402


class AutoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = make_fixture.build_light_gray(self.folder / "input.png")

    def _engine_reply(self, executable, source, ai_path, png_path, params, *args, **kwargs):
        if params["mode"] == "blackAndWhite":
            raise vc.VectorCraftError("VectorCraft returned no traced paths.")
        with Image.open(source) as handle:
            image = handle.convert("RGB")
        if params["palette"] == "limited":
            image = image.quantize(colors=params["colors"]).convert("RGB")
        image.save(png_path)
        colors = len(image.getcolors(maxcolors=256))
        return {"ai": ai_path, "png": png_path, "svg": None, "paths": colors,
                "anchors": colors * 4, "colors": colors, "seconds": 0.25, "warnings": []}

    def _trace(self, *, mode="auto", reply=None, reopened=None):
        scratch = self.folder / "job"
        scratch.mkdir(exist_ok=True)
        with patch.object(vc, "find_cli", return_value=Path(sys.executable)), \
                patch.object(vc, "_attempt", side_effect=reply or self._engine_reply) as attempt, \
                patch.object(vc.inspect_ai, "pdf_header", return_value={
                    "pdf_compatible": True, "private_data": True, "trailer_closed": True, "kb": 1,
                }), patch.object(vc, "inspect", return_value=reopened or {
                    "kinds": {"Path": 64}, "objects": 64, "warnings": [],
                }):
            result = vc.trace(self.source, scratch, preview=False, svg=False, mode=mode)
        return result, attempt

    def test_zero_paths_is_a_candidate_error(self):
        with patch.object(vc, "_run", return_value=[{
            "command": "imageTrace.makeAndExpand", "result": {"paths": 0},
        }]):
            with self.assertRaisesRegex(vc.VectorCraftError, "no traced paths"):
                vc._attempt(Path(sys.executable), self.source, self.folder / "out.ai",
                            self.folder / "out.png", {}, True, 1.0, {}, 1,
                            self.folder / "steps.json")

    def test_light_gray_auto_recovers_from_empty_bw(self):
        result, attempt = self._trace()
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual(result["mode"], "flat")
        self.assertEqual(result["fidelity"]["mae"], 0)
        self.assertEqual([a["ok"] for a in result["candidates"]], [False, True])
        self.assertIn("no traced paths", result["candidates"][0]["error"])
        self.assertIn("Skipped bw candidate", result["warnings"][0])

    def test_palette64_auto_preserves_colors_at_both_sizes(self):
        for width, height in ((512, 384), (2048, 1536)):
            with self.subTest(size=(width, height)):
                self.source = make_fixture.build_palette64(
                    self.folder / f"palette-{width}.png", width, height)
                with Image.open(self.source) as image:
                    self.assertEqual(len(image.getcolors(maxcolors=65)), 64)
                result, attempt = self._trace()
                self.assertEqual(attempt.call_count, 2)
                self.assertEqual(result["mode"], "flat")
                self.assertEqual(result["colors"], 64)
                self.assertEqual(result["fidelity"]["mae"], 0)
                self.assertEqual([a["colors"] for a in result["candidates"]], [64, 24])

    def test_failed_later_candidate_keeps_successful_first(self):
        self.source = make_fixture.build_palette64(self.source, 256, 192)

        def reply(*args, **kwargs):
            if args[4]["palette"] == "limited":
                raise vc.VectorCraftError("photo timed out")
            return self._engine_reply(*args, **kwargs)

        result, attempt = self._trace(reply=reply)
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual(result["mode"], "flat")
        self.assertFalse(result["candidates"][1]["ok"])
        self.assertEqual(result["candidates"][1]["error"], "photo timed out")

    def test_all_failed_candidates_fail_without_delivery(self):
        with patch.object(vc, "find_cli", return_value=Path(sys.executable)), \
                patch.object(vc, "_attempt", side_effect=vc.VectorCraftError("no traced paths")):
            target = self.folder / "result.ai"
            report = pipeline.convert(self.source, target,
                                      preview_path=self.folder / "result.png",
                                      svg_path=self.folder / "result.svg")
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("All tracing candidates failed", report["errors"][0])
        self.assertIn("bw:", report["errors"][0])
        self.assertIn("flat:", report["errors"][0])
        self.assertNotIn("outputs", report)
        self.assertFalse(any((self.folder / name).exists()
                             for name in ("result.ai", "result.png", "result.svg")))

    def test_explicit_bw_fails_without_fallback(self):
        with self.assertRaisesRegex(vc.VectorCraftError, "no traced paths"):
            self._trace(mode="bw")
        self.assertFalse((self.folder / "job" / "candidate-1-flat.png").exists())

    def test_io_errors_are_not_swallowed_as_candidate_failures(self):
        with self.assertRaisesRegex(PermissionError, "disk access denied"):
            self._trace(reply=PermissionError("disk access denied"))

    def test_fallback_still_requires_vector_only_reopen(self):
        with self.assertRaisesRegex(vc.VectorCraftError, "vector-only path artwork"):
            self._trace(reopened={"kinds": {"Path": 1, "Image": 1}, "objects": 2, "warnings": []})


if __name__ == "__main__":
    unittest.main()
