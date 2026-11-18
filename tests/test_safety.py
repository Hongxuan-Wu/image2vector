"""Regression checks for the converter and the two-host migration."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "shared")]

import install  # noqa: E402
from image2vector_core import pipeline, vc  # noqa: E402


class SafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "input.png"
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("test-marker", "remove this metadata")
        Image.new("RGBA", (8, 8), (10, 80, 160, 128)).save(self.source, pnginfo=metadata)

    def test_existing_ai_and_source_preview_are_protected(self):
        target = self.folder / "result.ai"
        target.write_bytes(b"existing AI")
        source_before = self.source.read_bytes()
        with patch.object(vc, "trace", side_effect=AssertionError("traced despite collision")):
            existing = pipeline.convert(self.source, target)
            overlap = pipeline.convert(self.source, self.folder / "new.ai", preview_path=self.source)
        self.assertEqual(existing["status"], "FAIL")
        self.assertEqual(overlap["status"], "FAIL")
        self.assertEqual(target.read_bytes(), b"existing AI")
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_atomic_delivery_refuses_a_late_collision(self):
        target = self.folder / "result.ai"
        target.write_bytes(b"another process")
        with self.assertRaises(FileExistsError):
            pipeline._deliver(self.source, target)
        self.assertEqual(target.read_bytes(), b"another process")

    def test_raster_allowlist_and_limits_precede_the_cli(self):
        fake = self.folder / "fake.png"
        fake.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        with patch.object(vc, "find_cli", side_effect=AssertionError("launched on bad input")):
            corrupt = pipeline.convert(fake, self.folder / "fake.ai")
            invalid_scale = pipeline.convert(self.source, self.folder / "invalid.ai", scale=float("nan"))
            with patch.object(vc, "MAX_PIXELS", 63):
                oversized = pipeline.convert(self.source, self.folder / "oversized.ai")
        for result in (corrupt, invalid_scale, oversized):
            self.assertEqual(result["status"], "FAIL")
        self.assertEqual(list(self.folder.glob("*.ai")), [])

    def test_normalization_preserves_alpha_and_strips_metadata(self):
        normalized = self.folder / "source.png"
        vc._raster_to_png(self.source, normalized)
        with Image.open(normalized) as image:
            self.assertNotIn("test-marker", image.info)
            self.assertEqual(image.getpixel((0, 0)), (10, 80, 160, 128))

    def test_cli_discovery_ignores_relative_paths(self):
        fake = self.folder / vc.CLI_NAME
        fake.touch()
        with self.assertRaises(vc.VectorCraftError):
            vc.find_cli(Path(".") / vc.CLI_NAME)
        with patch.object(vc, "_local_hints", return_value=[]), \
                patch.object(vc, "CLI_CANDIDATES", ()), patch.object(vc, "CLI_GLOBS", ()), \
                patch.dict("os.environ", {"PATH": ".", "VECTORCRAFT_CLI": ""}):
            with self.assertRaises(vc.VectorCraftError):
                vc.find_cli()

    def test_job_name_cannot_direct_writes_and_retries_get_new_jobs(self):
        first = pipeline.convert(self.source, self.folder / "retry.ai",
                                 job_name="../outside", scale=0)
        second = pipeline.convert(self.source, self.folder / "retry.ai",
                                  job_name="../outside", scale=0)
        self.assertNotEqual(first["job_dir"], second["job_dir"])
        for result in (first, second):
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(Path(result["job_dir"]).parent, self.folder / ".ai-work")

    def test_migrate_both_hosts_and_preserve_originals(self):
        profile = self.folder / "profile"
        previous = {}
        for edition in ("codex", "workbuddy"):
            old = profile / f".{edition}" / "skills" / "img2vector"
            old.mkdir(parents=True)
            content = json.dumps({"name": "img2vector", "engine": "vectorcraft-cli"})
            (old / "config.json").write_text(content, encoding="utf-8")
            (old / "original.txt").write_bytes(edition.encode())
            previous[old] = edition

        paths = install.install(("codex", "workbuddy"), profile, Path(sys.executable))
        for old, edition in previous.items():
            self.assertFalse(old.exists())
            installed = old.with_name("image2vector")
            self.assertIn(installed, paths)
            self.assertIn("name: image2vector", (installed / "SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue((installed / "scripts" / "image2vector.py").is_file())
            backups = list((profile / ".image2vector").glob(
                f"install-*/backups/{edition}/.{edition}/img2vector/original.txt",
            ))
            self.assertEqual([path.read_bytes() for path in backups], [edition.encode()])


if __name__ == "__main__":
    unittest.main()
