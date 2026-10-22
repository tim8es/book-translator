"""No duplicate hash passes within one sealed-corpus preflight."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import book
from workflow.source_coverage import VALIDATOR, coverage_integrity_errors
from workflow.status_cli import default_preflight


class PreflightEfficiencyTests(unittest.TestCase):
    def test_coverage_reuses_verified_hashes_without_reading_chapter_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "extracted" / "001.md"
            source.parent.mkdir()
            source.write_bytes(b"# One\n\nOriginal source.\n")
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            meta = {"source_format": "epub", "source": {"sha256": "a" * 64, "size_bytes": 17}}
            progress = {"chapters": [{"number": 1, "source_path": "extracted/001.md"}]}
            report = {
                "schema_version": 1, "validator": VALIDATOR, "state": "verified",
                "source_sha256": "a" * 64, "source_size_bytes": 17,
                "chapter_count": 1,
                "documents": [{
                    "path": "OPS/chapter.xhtml", "state": "verified",
                    "xhtml_sha256": "b" * 64, "reference_sha256": "c" * 64,
                    "text_tokens": 3, "text_blocks": 2, "chapter_count": 1,
                }],
                "extracted": [{"number": 1, "sha256": sha}],
            }
            path = root / "source-coverage.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(coverage_integrity_errors(root, meta, progress), [])
            with patch.object(Path, "read_bytes", side_effect=AssertionError("second hash read")):
                self.assertEqual(
                    coverage_integrity_errors(
                        root, meta, progress,
                        verified_extracted_sha256={"extracted/001.md": sha},
                    ), [],
                )
            report["extracted"][0]["sha256"] = "d" * 64
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertIn(
                "Chapter 1: source-coverage fingerprint mismatch",
                coverage_integrity_errors(
                    root, meta, progress,
                    verified_extracted_sha256={"extracted/001.md": sha},
                ),
            )

    def test_invalid_seal_disables_reuse_and_review_is_not_checked_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "books" / "demo"
            work.mkdir(parents=True)
            (work / "source-manifest.json").write_text("{}", encoding="utf-8")
            metadata = {"source_format": "epub", "source": {"storage_mode": "embedded"}}
            progress = {"chapters": []}
            corpus = SimpleNamespace(
                load_source_manifest=lambda _: {"extracted": []},
                verify_manifest=lambda *_: (_ for _ in ()).throw(ValueError("bad seal")),
            )
            with (
                patch.object(book, "validate_book", return_value=([], [])) as validate,
                patch.object(book, "load_book", return_value=(work, metadata, progress)),
                patch("workflow.status_cli.source_storage_mode", return_value="embedded"),
                patch("workflow.status_cli.normalize_structural_errors", return_value=[]),
                patch("workflow.status_cli.coverage_integrity_errors", return_value=["coverage error"]) as coverage,
                patch("workflow.status_cli.importlib.import_module", side_effect=lambda name: book if name == "book" else corpus),
            ):
                errors, state = default_preflight(root, "demo")
                validate.assert_called_once_with("demo", check_review_evidence=False)
                self.assertEqual(errors, ["coverage error"])
                self.assertEqual(state["state"], "invalid")
                self.assertIn("bad seal", state["error"])
                self.assertIsNone(coverage.call_args.kwargs.get("verified_extracted_sha256"))


if __name__ == "__main__":
    unittest.main()
