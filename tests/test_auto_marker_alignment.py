"""Synthetic tests for opt-in source-owned EPUB block alignment.

No historical reviewer PASS is re-used when accepted translation bytes change.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "scripts"))
import review_chunks as chunks
import book_runner as runner


def book():
    src, tgt = [], []
    for i in range(1, 15):
        marker = f"<!-- bt-block:{i:06d} -->\n"
        src.append(marker + (f"Source block {i}. Short English text. " * 37) + "\n\n")
        tgt.append(marker + (f"Целевой блок {i}. Длинное предложение с дополнениями. " *
                             (16 + i)) + "\n\n")
    return "".join(src), "".join(tgt)


def report():
    return json.dumps({
        "evidence_version": 2, "outcome": "PASS",
        "checks": dict.fromkeys(
            ("coverage", "meaning", "terminology", "style", "structure"), True),
        "findings": [], "style_guide": [],
    })


class MarkerAlignmentPureTests(unittest.TestCase):
    def setUp(self):
        self.source, self.translation = book()
        self.plan = chunks.plan_aligned(self.source, self.translation)

    def test_same_marker_sets_define_both_language_boundaries(self):
        self.assertEqual(self.plan["helper_revision"], chunks.ALIGNMENT_REVISION)
        self.assertGreaterEqual(len(self.plan["chunks"]), 2)
        chunks.validate_plan(self.plan, self.source, self.translation)
        for section in self.plan["chunks"]:
            src = self.source[slice(*section["source_core"])]
            tgt = self.translation[slice(*section["translation_core"])]
            self.assertEqual(
                chunks.BLOCK_MARKER.findall(src),
                chunks.BLOCK_MARKER.findall(tgt))
            self.assertTrue(src.strip())
            self.assertTrue(tgt.strip())
        self.assertEqual(
            "".join(self.source[slice(*x["source_core"])]
                    for x in self.plan["chunks"]), self.source)
        self.assertEqual(
            "".join(self.translation[slice(*x["translation_core"])]
                    for x in self.plan["chunks"]), self.translation)

    def test_changed_or_missing_marker_sequence_fails_closed(self):
        changed = self.translation.replace("bt-block:000008", "bt-block:000009")
        with self.assertRaisesRegex(ValueError, "not aligned"):
            chunks.plan_aligned(self.source, changed)
        changed = self.translation.replace("<!-- bt-block:000008 -->", "")
        with self.assertRaisesRegex(ValueError, "not aligned"):
            chunks.plan_aligned(self.source, changed)
        changed_source = self.source.replace("bt-block:000008", "bt-block:000007")
        with self.assertRaisesRegex(ValueError, "not aligned"):
            chunks.plan_aligned(changed_source, self.translation)

    def test_modified_aligned_boundary_is_not_valid_evidence(self):
        forged = json.loads(json.dumps(self.plan))
        forged["chunks"][0]["source_core"][1] += 1
        with self.assertRaisesRegex(ValueError, "cover|partition"):
            chunks.validate_plan(forged, self.source, self.translation)
        forged = json.loads(json.dumps(self.plan))
        forged["helper_revision"] = chunks.HELPER_REVISION
        with self.assertRaises(ValueError):
            chunks.validate_plan(forged, self.source, self.translation)

    def test_legacy_plan_is_unchanged_with_opt_in_available(self):
        legacy = chunks.plan(self.source, self.translation)
        self.assertEqual(legacy["helper_revision"], chunks.HELPER_REVISION)
        chunks.validate_plan(legacy, self.source, self.translation)
        self.assertEqual(
            chunks.plan_aligned("First.\n\nSecond.\n\nThird.",
                                "Первое.\n\nВторое.\n\nТретье.")["helper_revision"],
            chunks.HELPER_REVISION)


class MarkerAlignmentRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.slug = "synthetic"
        self.book = self.root / "books" / self.slug
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "translated").mkdir()
        self.source, self.translation = book()
        self.chapter = {
            "unit_id": "chapter-000001", "number": 1,
            "source_path": "extracted/unit.md", "translation_path": "translated/unit.md",
            "status": "translated",
        }
        (self.book / self.chapter["source_path"]).write_text(self.source, "utf-8")
        (self.book / self.chapter["translation_path"]).write_text(self.translation, "utf-8")
        (self.book / "metadata.json").write_text(json.dumps({
            "title": "Synthetic", "author": "Test",
            "source_language": "en", "target_language": "ru",
        }), "utf-8")
        (self.book / "glossary.md").write_text("", "utf-8")
        (self.book / "style-guide.md").write_text("", "utf-8")
        self.status = {
            "workflow_revision": "f"*40, "model_calls_started": 0,
            "state": "running", "phase": "claimed",
            "active": {"unit_id": "chapter-000001", "chapter": 1,
                       "role": "reviewer", "session_id": "synthetic-review"},
        }
        self.patchers = [
            patch.object(runner, "ROOT", self.root),
            patch.object(runner, "role_contracts",
                         return_value=("Pinned independent reviewer", ["AGENTS.md"])),
            patch.object(runner, "checkpoint"),
            patch.object(runner, "full_shared_context", return_value=("", "")),
            patch.object(runner, "model_glossary_context", return_value=""),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)

    def run_once(self, alignment_mode):
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_MARKER_ALIGNMENT": alignment_mode}), \
             patch.object(runner, "complete_for_job", return_value=report()):
            return runner.process_review_chunks(
                "a"*32, self.slug, self.status, self.root, self.chapter,
                previous=None, owned={"session_id": "synthetic-review"})

    def stage(self):
        return runner.read_json(
            runner.review_chunk_stage_path(self.slug, self.chapter["unit_id"]))

    def test_new_aligned_checkpoint_resumes_even_if_flag_switches_off(self):
        self.assertIsNone(self.run_once("on"))
        self.assertEqual(self.stage()["plan"]["helper_revision"],
                         chunks.ALIGNMENT_REVISION)
        self.assertEqual(len(self.stage()["completed"]), 1)
        self.assertIsNone(self.run_once("off"))
        self.assertEqual(len(self.stage()["completed"]), 2)
        self.assertEqual(self.status["model_calls_started"], 2)

    def test_existing_legacy_checkpoint_does_not_silently_upgrade(self):
        legacy = chunks.plan(self.source, self.translation)
        stage = chunks.new_stage(
            legacy, self.chapter["unit_id"], self.status["workflow_revision"])
        path = runner.review_chunk_stage_path(self.slug, self.chapter["unit_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        runner.store_json(path, stage)
        self.assertIsNone(self.run_once("on"))
        self.assertEqual(self.stage()["plan"]["helper_revision"],
                         chunks.HELPER_REVISION)

    def test_changed_translation_invalidates_all_prior_chunks_and_needs_review(self):
        self.assertIsNone(self.run_once("on"))
        (self.book / self.chapter["translation_path"]).write_text(
            self.translation.replace("Целевой блок 8.", "Изменённый блок 8."),
            "utf-8")
        self.assertIsNone(self.run_once("off"))
        self.assertEqual(self.stage()["plan"]["helper_revision"],
                         chunks.ALIGNMENT_REVISION)
        self.assertEqual(len(self.stage()["completed"]), 1)
        self.assertEqual(len(self.status["review_chunk_invalidations"]), 1)


if __name__ == "__main__":
    unittest.main()
