"""Regression: a reviewer quote may overlap source-owned EPUB markers.

The source markers cannot be edited or automatically treated as resolved.
An exact blocked failed-correction job may resume once for source-safe
partial patches and canonical review deferral.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import book_runner as auto


class ProtectedReviewerPatchTests(unittest.TestCase):
    JOB = "05eed6ea814d3b5eec014c6d05b39e3f"

    def blocked(self):
        return {
            "schema_version": 1, "job_id": self.JOB,
            "book_slug": "example-book", "state": "blocked",
            "phase": "model-blocked", "active": None, "failures": 3,
            "blocked_unit_id": "chapter-000001",
            "workflow_revision": "a" * 40,
            "source_sha256": "b" * 64,
            "last_error": (
                "correction patch touches a protected EPUB marker or Markdown link boundary"
            ),
        }

    def test_exact_error_one_shot_with_all_source_id_checks(self):
        item = self.blocked()
        self.assertTrue(auto.recoverable_protected_correction_blocked(item))
        for fields in (
            {"last_error": "unknown model error"},
            {"state": "running"},
            {"phase": "operator-intervention-required"},
            {"failures": 2},
            {"active": {"role": "corrector"}},
            {"protected_correction_retries": 1},
            {"source_sha256": "bad"},
            {"workflow_revision": "bad"},
            {"blocked_unit_id": "../unsafe"},
            {"book_slug": "../unsafe"},
            {"provider_retry": {"attempts": 1}},
        ):
            with self.subTest(fields=fields):
                self.assertFalse(auto.recoverable_protected_correction_blocked(
                    {**item, **fields}))

    def test_ready_queue_recovers_only_exact_fixed_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request_dir = root / "intake" / "auto" / self.JOB
            request_dir.mkdir(parents=True)
            (request_dir / "request.json").write_text("{}", encoding="utf-8")
            status_dir = root / "automation" / "status"
            status_dir.mkdir(parents=True)
            file = status_dir / (self.JOB + ".json")
            item = self.blocked()
            file.write_text(json.dumps(item), encoding="utf-8")
            with patch.object(auto, "ROOT", root), \
                 patch.object(auto, "book_key", return_value="example-book"):
                self.assertEqual(
                    auto.ready_jobs(),
                    [{"job_id": self.JOB, "book_key": "example-book"}]
                )
                item["protected_correction_retries"] = 1
                file.write_text(json.dumps(item), encoding="utf-8")
                self.assertEqual(auto.ready_jobs(), [])

    def test_disjoint_language_patch_keeps_original_protected_marker(self):
        source = (
            "<!-- bt-block:000001 -->\n"
            "First term <!-- bt-inline:000001:start -->Second"
            "<!-- bt-inline:000001:end --> Third\n"
        )
        edited = auto.apply_correction_patches(source, [{
            "target_anchor": {"quote": "First term", "occurrence": 0},
            "operation": "replace",
            "replacement": "Первый термин",
        }])
        self.assertIn("Первый термин", edited)
        auto.check_markers(source, edited)
        with self.assertRaisesRegex(auto.JobError, "protected EPUB marker"):
            auto.apply_correction_patches(source, [{
                "target_anchor": {
                    "quote": "Second<!-- bt-inline:000001:end --> Third",
                    "occurrence": 0,
                },
                "operation": "replace",
                "replacement": "новый текст",
            }])
        with self.assertRaisesRegex(auto.JobError, "protected EPUB marker"):
            auto.apply_correction_patches(source, [{
                "target_anchor": {"quote": "<!-- bt-inline:000001:start -->",
                                  "occurrence": 0},
                "operation": "replace",
                "replacement": "не менять",
            }])


class ProtectedReviewerBatchTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        slug = "fixture"
        book = root / "books" / slug
        (book / "extracted").mkdir(parents=True)
        (book / "translated").mkdir()
        (book / "glossary.md").write_text("terms\n", encoding="utf-8")
        (book / "style-guide.md").write_text("style\n", encoding="utf-8")
        original = (
            "<!-- bt-block:000001 -->\n"
            "First term <!-- bt-inline:000001:start -->Second"
            "<!-- bt-inline:000001:end --> Third\n"
        )
        (book / "extracted" / "001.md").write_text(original, encoding="utf-8")
        (book / "translated" / "001.md").write_text(original, encoding="utf-8")
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
        chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "source_path": "extracted/001.md",
            "translation_path": "translated/001.md",
            "status": "translated",
            "translation_acceptance": {
                "source_sha256": digest, "translation_sha256": digest,
            },
        }
        status = {
            "state": "running", "phase": "claimed",
            "active": {"chapter": 1, "role": "corrector", "session_id": "test"},
            "workflow_revision": "a" * 40,
            "model_calls_started": 0,
        }
        findings = [
            {"id": "safe-1", "resolved": False},
            {"id": "protected-1", "resolved": False},
        ]
        record = {
            "record_id": "review-1", "source_sha256": digest,
            "translation_sha256": digest, "outcome": "CORRECTIONS_REQUIRED",
            "quality_report": {"evidence_version": 2, "findings": findings},
        }
        safe = {
            "finding_id": "safe-1", "operation": "replace",
            "target_anchor": {"quote": "First term", "occurrence": 0},
            "replacement": "Первый термин",
        }
        protected = {
            "finding_id": "protected-1", "operation": "replace",
            "target_anchor": {
                "quote": "Second<!-- bt-inline:000001:end --> Third",
                "occurrence": 0,
            }, "replacement": "Заменённый маркер",
        }
        stage = {
            "patches": [], "glossary": [], "style_guide": [],
        }
        stage_path = book / ".workflow" / "auto-correction-batches" / "chapter-000001.json"
        return root, slug, chapter, status, record, stage, stage_path, safe, protected

    def execute(self, fixture, patches):
        root, slug, chapter, status, record, stage, stage_path, *_ = fixture
        with patch.object(auto, "ROOT", root), \
             patch.object(auto, "load_correction_stage",
                          return_value=(stage_path, stage)), \
             patch.object(auto, "context", return_value=("sys", "user", "")), \
             patch.object(auto, "role_contracts",
                          return_value=("test", ["AGENTS.md"])), \
             patch.object(auto, "checkpoint"), \
             patch.object(auto, "complete_for_job", return_value="{}"), \
             patch.object(auto, "parse_correction_patches",
                          return_value=(patches, [], [])):
            return auto.process_correction_batches(
                "f" * 32, slug, status, root, chapter, record)

    def test_safe_patch_applied_with_marker_crossing_finding_still_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            _, _, _, status, _, _, stage_path, safe, protected = fixture
            result = self.execute(fixture, [safe, protected])
            self.assertIsNotNone(result)
            self.assertIn("Первый термин", result[0])
            self.assertIn("<!-- bt-inline:000001:end -->", result[0])
            self.assertNotIn("Заменённый маркер", result[0])
            self.assertEqual(status["correction_overlap_deferred"]["unapplied_finding_ids"],
                             ["protected-1"])
            staged = json.loads(stage_path.read_text(encoding="utf-8"))
            self.assertEqual([p["finding_id"] for p in staged["patches"]],
                             ["safe-1"])

    def test_only_protected_patch_fails_without_changing_literary_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(directory)
            root, slug, chapter, _, _, _, stage_path, _, protected = fixture
            artifact = root / "books" / slug / chapter["translation_path"]
            original = artifact.read_bytes()
            with self.assertRaisesRegex(auto.ModelError, "intersect protected EPUB"):
                self.execute(fixture, [protected])
            self.assertEqual(artifact.read_bytes(), original)
            self.assertFalse(stage_path.exists())


if __name__ == "__main__":
    unittest.main()
