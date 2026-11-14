"""Regression checks for source-bound translation completeness and safe partial patches."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner


class _PlainHelper:
    def plan(self, source):
        return None

    def spans(self, plan):
        result = [p for p in plan["parts"] if p["kind"] == "text"]
        ids = [p["id"] for p in result]
        assert len(set(ids)) == len(ids)
        return [{"id": p["id"], "source": p["original"]} for p in result]


class CoverageRecoveryTests(unittest.TestCase):
    def test_source_plan_is_bounded_and_preserves_every_source_byte(self):
        source = "\n".join(["Title", ""]) + "\n\n".join(
            f"Paragraph {i}. " + ("This sentence carries meaning. " * 17)
            for i in range(14)
        ) + "\n"
        result = runner.bounded_source_plan(source, _PlainHelper(), limit=1100)
        chunks = _PlainHelper().spans(result)
        self.assertGreater(len(chunks), 4)
        self.assertTrue(all(len(p["source"]) <= 1100 for p in chunks))
        self.assertEqual(result["source_sha256"], hashlib.sha256(source.encode()).hexdigest())
        restored = "".join(
            p["before"] + p["original"] + p["after"]
            if p["kind"] == "text" else p["value"] for p in result["parts"])
        self.assertEqual(restored, source)
        self.assertEqual([p["id"] for p in chunks],
                         [f"s{i:05d}" for i in range(1, len(chunks) + 1)])

    def test_detects_large_missing_chapter_and_short_segment(self):
        source = ("A source paragraph has several sentences.\n\n" * 95)
        self.assertTrue(runner.suspicious_translation_coverage(source, "Краткий текст.\n"))
        self.assertFalse(runner.suspicious_translation_coverage(source, source))
        self.assertEqual(
            runner.incomplete_structural_batch(
                [{"id": "s00001", "source": source}],
                {"s00001": "Недостаточный перевод."}), ["s00001"])
        self.assertEqual(
            runner.incomplete_structural_batch(
                [{"id": "s00001", "source": source}],
                {"s00001": source}), [])

    def test_overlapping_finding_patches_accept_only_safe_partial_for_new_review(self):
        src = "An important source phrase.\n"
        translation = "An incorrect target phrase.\n"
        digest_src = hashlib.sha256(src.encode()).hexdigest()
        digest_dst = hashlib.sha256(translation.encode()).hexdigest()
        record = {"record_id": "review-123", "source_sha256": digest_src,
                  "translation_sha256": digest_dst, "outcome": "CORRECTIONS_REQUIRED",
                  "quality_report": {"evidence_version": 2, "findings": [
                      {"id": "finding-a", "resolved": False},
                      {"id": "finding-b", "resolved": False},
                  ]}}
        patches = [
            {"finding_id": "finding-a", "operation": "replace",
             "target_anchor": {"quote": "incorrect target", "occurrence": 0},
             "replacement": "correct target"},
            {"finding_id": "finding-b", "operation": "replace",
             "target_anchor": {"quote": "incorrect target", "occurrence": 0},
             "replacement": "alternative target"},
        ]
        chapter = {"unit_id": "chapter-000001", "number": 1,
                   "source_path": "extracted/one.md",
                   "translation_path": "translated/one.md",
                   "translation_acceptance": {
                       "source_sha256": digest_src, "translation_sha256": digest_dst}}
        status = {"workflow_revision": "a" * 40, "model_calls_started": 0,
                  "active": {"role": "corrector", "session_id": "owner", "chapter": 1}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "example"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            (book / chapter["source_path"]).write_text(src, encoding="utf-8")
            (book / chapter["translation_path"]).write_text(translation, encoding="utf-8")
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "context", return_value=("sys", "user", src)), \
                 patch.object(runner, "role_contracts", return_value=("contract", [])), \
                 patch.object(runner, "complete", return_value="model json") as complete, \
                 patch.object(runner, "parse_correction_patches",
                              return_value=(patches, [], [])), \
                 patch.object(runner, "checkpoint") as checkpoint:
                result = runner.process_correction_batches(
                    "b" * 32, "example", status, root, chapter, record)
            self.assertIsNotNone(result)
            self.assertEqual(result[0], "An correct target phrase.\n")
            self.assertEqual(status["correction_batch_progress"]["completed"], 1)
            self.assertEqual(status["correction_batch_progress"]["total"], 2)
            self.assertEqual(
                status["correction_overlap_deferred"]["unapplied_finding_ids"],
                ["finding-b"])
            self.assertEqual(checkpoint.call_count, 2)
            complete.assert_called_once()
            # The canonical source/translation were NOT modified in this helper.
            self.assertEqual((book / chapter["translation_path"]).read_text("utf-8"),
                             translation)


    def test_operator_plan_reopens_exact_current_rejected_review_once(self):
        import sys
        from types import ModuleType
        from unittest.mock import patch
        workflow = ModuleType("workflow")
        workflow.__path__ = []
        attention = ModuleType("workflow.attention")
        attention.validate_attention = lambda data, revision: data
        unit = "chapter-000028"
        source_hash, trans_hash = "a" * 64, "b" * 64
        record_id = "c" * 32
        review = {"unit_id": unit, "record_id": record_id,
                  "source_sha256": source_hash,
                  "translation_sha256": trans_hash,
                  "outcome": "CORRECTIONS_REQUIRED", "sequence": 1}
        chapter = {
            "unit_id": unit, "number": 28, "status": "translated",
            "translation_acceptance": {
                "source_sha256": source_hash, "translation_sha256": trans_hash}}
        queue_entry = {"unit_id": unit, "source_sha256": source_hash,
                       "translation_sha256": trans_hash,
                       "review_record_id": record_id,
                       "reason": "review-round-limit",
                       "created_at": "2026-11-13T12:30:00+00:00"}
        plan = {"schema_version": 1, "book_slug": "sample",
                "workflow_revision": "d" * 40,
                "authorized_by": "user-request-finish-book",
                "authorized_at": "2026-11-13T13:40:00+00:00",
                "unit_ids": [unit], "review_limit": 16,
                "correction_limit": 12}
        status = {"state": "running", "phase": "reviewer-completed",
                  "workflow_revision": "d" * 40, "active": None,
                  "deferred_review_units": [unit]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / "books" / "sample" / ".workflow"
            wf.mkdir(parents=True)
            (wf / "operator-review-reopen.json").write_text(
                json.dumps(plan), encoding="utf-8")
            qpath = wf / "needs-attention.json"
            qpath.write_text(json.dumps({
                "schema_version": 1, "workflow_revision": "d" * 40,
                "entries": [queue_entry]}), encoding="utf-8")
            with patch.dict(sys.modules, {
                     "workflow": workflow, "workflow.attention": attention}), \
                 patch.object(runner, "ROOT", root), \
                 patch.object(runner, "cli",
                              return_value=json.dumps({"claims": [], "errors": []})), \
                 patch.object(runner, "chapter_data",
                              return_value=({}, {"records": [review]}, [chapter])), \
                 patch.object(runner, "checkpoint") as checkpoint:
                self.assertTrue(runner.apply_operator_review_reopen(
                    "e" * 32, "sample", status))
                self.assertFalse(runner.apply_operator_review_reopen(
                    "e" * 32, "sample", status))
                checkpoint.assert_called_once()
            self.assertEqual(json.loads(qpath.read_text())["entries"], [])
            self.assertEqual(status["operator_review_limits"][unit], 16)
            self.assertEqual(status["operator_correction_limits"][unit], 12)
            self.assertEqual(status["deferred_review_units"], [])
            self.assertEqual(status["phase"], "operator-reviewed-reopen-applied")
            self.assertNotIn("outcome", status)
            self.assertEqual(status["operator_recovery_audit"][0]
                             ["evidence"][0]["record_id"], record_id)

    def test_operator_plan_rejects_stale_review_without_mutating_queue(self):
        import sys
        from types import ModuleType
        from unittest.mock import patch
        workflow = ModuleType("workflow")
        workflow.__path__ = []
        attention = ModuleType("workflow.attention")
        attention.validate_attention = lambda data, revision: data
        unit = "chapter-000025"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / "books" / "sample" / ".workflow"
            wf.mkdir(parents=True)
            (wf / "operator-review-reopen.json").write_text(json.dumps({
                "schema_version": 1, "book_slug": "sample",
                "workflow_revision": "d" * 40,
                "authorized_by": "user-request-finish-book",
                "authorized_at": "2026-11-13T13:40:00+00:00",
                "unit_ids": [unit], "review_limit": 16,
                "correction_limit": 12}), encoding="utf-8")
            queue = {"schema_version": 1, "workflow_revision": "d" * 40,
                     "entries": [{
                         "unit_id": unit, "source_sha256": "a" * 64,
                         "translation_sha256": "b" * 64,
                         "review_record_id": "c" * 32,
                         "reason": "review-round-limit",
                         "created_at": "2026-11-13T12:30:00+00:00"}]}
            path = wf / "needs-attention.json"
            path.write_text(json.dumps(queue), encoding="utf-8")
            before = path.read_bytes()
            status = {"workflow_revision": "d" * 40, "active": None}
            chapter = {"unit_id": unit, "number": 25, "status": "translated",
                       "translation_acceptance": {
                           "source_sha256": "a" * 64,
                           "translation_sha256": "b" * 64}}
            record = {"unit_id": unit, "record_id": "f" * 32,
                      "source_sha256": "a" * 64, "translation_sha256": "b" * 64,
                      "outcome": "CORRECTIONS_REQUIRED", "sequence": 1}
            with patch.dict(sys.modules, {
                     "workflow": workflow, "workflow.attention": attention}), \
                 patch.object(runner, "ROOT", root), \
                 patch.object(runner, "cli",
                              return_value=json.dumps({"claims": [], "errors": []})), \
                 patch.object(runner, "chapter_data",
                              return_value=({}, {"records": [record]}, [chapter])), \
                 patch.object(runner, "checkpoint") as checkpoint:
                with self.assertRaisesRegex(runner.JobError, "no longer"):
                    runner.apply_operator_review_reopen("e" * 32, "sample", status)
                checkpoint.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn("operator_reopen_manifest_sha256", status)

    def test_operator_retry_budget_is_book_scoped_bounded_and_does_not_waive_review(self):
        unit = "chapter-000025"
        chapter = {"unit_id": unit, "status": "translated"}
        ledger = {"records": []}
        status = {"correction_rounds": {unit: 4},
                  "correction_extensions": {unit: 1}}
        self.assertFalse(runner.correction_allowed(status, ledger, chapter))
        status["operator_correction_limits"] = {unit: 9}
        status["operator_review_limits"] = {unit: 12}
        self.assertTrue(runner.correction_allowed(status, ledger, chapter))
        self.assertEqual(runner.operator_unit_limit(
            status, "review", unit, runner.MAX_ACCEPTED_REVIEWS_PER_UNIT), 12)
        self.assertEqual(runner.operator_unit_limit(
            status, "review", "chapter-000022",
            runner.MAX_ACCEPTED_REVIEWS_PER_UNIT), runner.MAX_ACCEPTED_REVIEWS_PER_UNIT)
        status["correction_rounds"][unit] = 9
        self.assertFalse(runner.correction_allowed(status, ledger, chapter))
        status["operator_review_limits"][unit] = 0
        with self.assertRaises(runner.JobError):
            runner.operator_unit_limit(
                status, "review", unit, runner.MAX_ACCEPTED_REVIEWS_PER_UNIT)


    def test_non_null_omission_anchor_keeps_reviewer_coordinates(self):
        finding = {
            "id": "omission-1", "resolved": False, "category": "omission",
            "translation_anchor": {"quote": "target phrase", "occurrence": 0},
        }
        # Some models redundantly include a reviewer-equivalent insertion
        # address for a finding categorized as omission. It cannot redirect
        # the edit because the reviewer-owned target is used.
        harmless = json.dumps({
            "patches": [{
                "finding_id": "omission-1", "operation": "replace",
                "insertion_anchor": finding["translation_anchor"],
                "replacement": "correct phrase",
            }], "glossary": [], "style_guide": [],
        })
        patches, _, _ = runner.parse_correction_patches(harmless, [finding])
        self.assertEqual(patches[0]["target_anchor"], finding["translation_anchor"])
        self.assertEqual(runner.apply_correction_patches(
            "before target phrase after", patches), "before correct phrase after")
        wrong = json.dumps({
            "patches": [{
                "finding_id": "omission-1", "operation": "replace",
                "insertion_anchor": {"quote": "before", "occurrence": 0},
                "replacement": "correct phrase",
            }], "glossary": [], "style_guide": [],
        })
        with self.assertRaises(runner.PatchAnchorMismatch):
            runner.parse_correction_patches(wrong, [finding])

    def test_correction_schema_repair_uses_review_binding_without_unsafe_write(self):
        source = "Short source sentence.\n"
        translation = "Bad target sentence.\n"
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        target_hash = hashlib.sha256(translation.encode()).hexdigest()
        anchor = {"quote": "Bad target", "occurrence": 0}
        review = {"record_id": "f" * 32, "source_sha256": source_hash,
                  "translation_sha256": target_hash, "outcome": "CORRECTIONS_REQUIRED",
                  "quality_report": {"evidence_version": 2, "findings": [{
                      "id": "omission-1", "resolved": False,
                      "translation_anchor": anchor, "category": "omission",
                  }]}}
        chapter = {
            "unit_id": "chapter-000001", "number": 1, "status": "translated",
            "source_path": "extracted/001.md",
            "translation_path": "translated/001.md",
            "translation_acceptance": {
                "source_sha256": source_hash, "translation_sha256": target_hash},
        }
        incorrect = json.dumps({"patches": [{
            "finding_id": "omission-1", "operation": "replace",
            "insertion_anchor": {"quote": "sentence", "occurrence": 0},
            "replacement": "Good target",
        }], "glossary": [], "style_guide": []})
        repaired = json.dumps({"patches": [{
            "finding_id": "omission-1", "operation": "replace",
            "replacement": "Good target",
        }], "glossary": [], "style_guide": []})
        status = {
            "state": "running", "workflow_revision": "d" * 40,
            "model_calls_started": 0,
            "active": {"role": "corrector", "chapter": 1, "session_id": "owner"},
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books" / "sample"
            (book / "translated").mkdir(parents=True)
            (book / "extracted").mkdir(parents=True)
            (book / chapter["source_path"]).write_text(source, encoding="utf-8")
            (book / chapter["translation_path"]).write_text(translation, encoding="utf-8")
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "context", return_value=("sys", "user", source)), \
                 patch.object(runner, "role_contracts",
                              return_value=("sys", ["AGENTS.md"])), \
                 patch.object(runner, "complete",
                              side_effect=[incorrect, repaired]) as model, \
                 patch.object(runner, "load_public_correction_helper",
                              return_value=type("TypedProtocolStub", (), {
                                  "failure_code": staticmethod(lambda error: "wrong_anchor"),
                                  "repair_guidance": staticmethod(
                                      lambda selected, error: json.dumps({
                                          "items": [{"finding_id": f["id"],
                                                     "reviewer_bound": True}
                                                    for f in selected],
                                          "error": error})),
                                  "validate_replacement": staticmethod(lambda p, f: None),
                                  "validate_intent": staticmethod(lambda p, f: None),
                                  "CorrectionProtocolError": ValueError,
                              })) , \
                 patch.object(runner, "checkpoint") as checkpoint:
                result = runner.process_correction_batches(
                    "e" * 32, "sample", status, root, chapter, review)
            self.assertEqual(result[0], "Good target sentence.\n")
            self.assertEqual(status["model_calls_started"], 2)
            self.assertEqual(model.call_count, 2)
            self.assertEqual(checkpoint.call_count, 3)
            repair_prompt = model.call_args_list[1].args[2]
            self.assertIn("reviewer_bound", repair_prompt)
            self.assertIn("omission-1", repair_prompt)
            self.assertEqual(
                (book / chapter["translation_path"]).read_text("utf-8"),
                translation, "only canonical acceptance may mutate the translation")


    def test_minor_local_register_followup_uses_checked_source_delta(self):
        source = "Original meaning.\n\nSecond paragraph.\n"
        translation = "Исправленный перевод.\n\nВторой абзац.\n"
        finding = {
            "id": "register-1", "category": "register",
            "severity": "minor", "resolved": False,
            "source_anchor": {"quote": "Original meaning.", "occurrence": 0},
            "translation_anchor": {"quote": "Старый перевод.", "occurrence": 0},
        }
        prior = {
            "record_id": "f" * 32, "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "translation_sha256": "e" * 64,
            "quality_report": {
                "evidence_version": 2,
                "checks": {"coverage": True, "meaning": True,
                           "terminology": True, "style": True, "structure": True},
                "findings": [finding],
            },
        }
        chapter = {"unit_id": "chapter-000001", "number": 1}
        delta = {
            "advisory_only": True, "mode": "focused_candidate",
            "unit_id": chapter["unit_id"],
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "translation_sha256": hashlib.sha256(translation.encode()).hexdigest(),
            "previous_record_id": prior["record_id"],
            "sections": [{
                "new_paragraph_range": [1, 1], "preceding_context": "",
                "following_context": "",
            }],
        }
        with patch.object(runner, "cli", return_value=json.dumps(delta)):
            context = runner.focused_review_context(
                "sample", chapter, prior, source, translation)
            self.assertIsNotNone(context)
            self.assertEqual(context["previous_record_id"], "f" * 32)
            self.assertEqual(len(context["changed_translation_sections"]), 1)
            self.assertEqual(len(context["unresolved_findings"]), 1)
            prior["quality_report"]["findings"][0]["severity"] = "major"
            self.assertIsNone(runner.focused_review_context(
                "sample", chapter, prior, source, translation))
            prior["quality_report"]["findings"][0]["severity"] = "minor"
            prior["quality_report"]["findings"][0]["category"] = "structure"
            self.assertIsNone(runner.focused_review_context(
                "sample", chapter, prior, source, translation))


if __name__ == "__main__":
    unittest.main()
