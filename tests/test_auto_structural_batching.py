"""Bounded source-identified model batching and audited stage migration tests."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "automation"))

import book_runner
from workflow import structural_translation as helper


class BoundedBatchTests(unittest.TestCase):
    def test_larger_batches_obey_count_and_total_text_budget(self):
        source = "".join(
            f"<!-- bt-block:{i:06d} -->\n" + "A sentence with useful words. " * 5
            for i in range(1, 65))
        plan = book_runner.bounded_source_plan(source, helper)
        default = helper.next_spans(plan, {})
        larger = helper.next_spans(plan, {}, limit=book_runner.STRUCTURAL_MODEL_SEGMENTS_PER_CALL)
        self.assertEqual(len(default), 3)
        self.assertGreater(len(larger), len(default))
        self.assertLessEqual(len(larger), 24)
        self.assertLessEqual(sum(len(item["source"]) for item in larger), 3500)
        self.assertEqual([item["id"] for item in larger],
                         [item["id"] for item in helper.spans(plan)[:len(larger)]])
        completed = {item["id"]: item["source"] for item in larger}
        remaining = helper.next_spans(plan, completed, limit=24)
        self.assertTrue(remaining)
        self.assertNotIn(remaining[0]["id"], completed)
        self.assertLessEqual(sum(len(item["source"]) for item in remaining), 3500)

    def test_single_large_fragment_not_overpacked(self):
        source = (("A very long prose sentence. " * 70).rstrip() + "\n\n" +
                  ("Another longer prose sentence. " * 65).rstrip())
        plan = book_runner.bounded_source_plan(source, helper)
        spans = helper.next_spans(plan, {}, limit=24)
        self.assertLessEqual(sum(len(x["source"]) for x in spans), 3500)
        self.assertTrue(all(0 < len(x["source"]) <= 1600 for x in spans))
        self.assertEqual(helper.render(plan, {
            p["id"]: p["source"] for p in helper.spans(plan)
        }), source.rstrip() + "\n")


class StageMigrationTests(unittest.TestCase):
    def fixture(self, root):
        slug = "fixture-book"
        chapter = {"unit_id": "chapter-000001"}
        status = {"workflow_revision": "a" * 40}
        source = "<!-- bt-block:000001 -->\nOriginal test phrase."
        plan = book_runner.bounded_source_plan(source, helper)
        path = (root / "books" / slug / ".workflow" /
                "auto-structural-translation" / "chapter-000001.json")
        path.parent.mkdir(parents=True)
        first = helper.spans(plan)[0]["id"]
        stage = {
            "schema_version": 1,
            "unit_id": chapter["unit_id"],
            "source_sha256": plan["source_sha256"],
            "workflow_revision": status["workflow_revision"],
            "helper_revision": "d11a30b306a8bac236b73f25ae2f38e89745aab1",
            "segments": {first: "Переведённая проверочная фраза."},
            "glossary": [],
            "style_guide": [],
            "segment_plan_sha256": hashlib.sha256(
                json.dumps(plan["parts"], sort_keys=True,
                           ensure_ascii=False).encode("utf-8")).hexdigest(),
        }
        book_runner.store_json(path, stage)
        return slug, chapter, status, plan, path, stage

    def test_immutable_stage_keeps_accepted_segments_during_helper_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(book_runner, "ROOT", root):
                slug, chapter, status, plan, path, original = self.fixture(root)
                actual_path, migrated = book_runner.load_structural_stage(
                    slug, chapter, status, helper, plan)
                self.assertEqual(actual_path, path)
                self.assertEqual(migrated["segments"], original["segments"])
                self.assertEqual(migrated["source_sha256"], original["source_sha256"])
                self.assertEqual(migrated["segment_plan_sha256"],
                                 original["segment_plan_sha256"])
                self.assertEqual(migrated["helper_revision"],
                                 book_runner.PUBLIC_STRUCTURAL_HELPER_REVISION)
                self.assertEqual(book_runner.read_json(path), migrated)
                audit = status["structural_helper_migrations"]
                self.assertEqual(len(audit), 1)
                self.assertEqual(audit[0]["persisted_segments"], 1)
                _, reopened = book_runner.load_structural_stage(
                    slug, chapter, status, helper, plan)
                self.assertEqual(reopened, migrated)
                self.assertEqual(len(status["structural_helper_migrations"]), 1)

    def test_mismatched_plan_cannot_migrate_an_existing_translation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(book_runner, "ROOT", root):
                slug, chapter, status, plan, path, original = self.fixture(root)
                changed = dict(plan)
                changed["parts"] = [dict(part) for part in plan["parts"]]
                changed["parts"][1]["original"] = "Altered source text."
                with self.assertRaisesRegex(book_runner.JobError,
                                            "stale or invalid"):
                    book_runner.load_structural_stage(
                        slug, chapter, status, helper, changed)
                self.assertEqual(book_runner.read_json(path), original)


if __name__ == "__main__":
    unittest.main()
