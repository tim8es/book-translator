"""Regression: a reviewer quote may overlap source-owned EPUB markers.

The source markers cannot be edited or automatically treated as resolved.
An exact blocked failed-correction job may resume once for source-safe
partial patches and canonical review deferral.
"""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
