"""End-to-end adapter regression for typed protocol repair and safe book progress."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker
from omniroute import ModelError


class _Protocol:
    class CorrectionProtocolError(ValueError):
        code = "invalid"

    @staticmethod
    def failure_code(value):
        return {
            "corrector replacement exceeds maximum length": "invalid_replacement",
            "corrector insertion requires nonempty replacement": "empty_insertion",
            "corrector returned no actionable patches": "missing_patches",
        }.get(value)

    @staticmethod
    def repair_guidance(findings, err):
        return json.dumps({
            "finding_ids": [f["id"] for f in findings],
            "failure_code": _Protocol.failure_code(err),
            "requirements": "short local reviewed replacement only",
        })

    @staticmethod
    def validate_replacement(patch, finding):
        if not isinstance(patch["replacement"], str):
            raise _Protocol.CorrectionProtocolError()
        if len(patch["replacement"]) > 10_000:
            raise _Protocol.CorrectionProtocolError()

    @staticmethod
    def validate_intent(patch, finding):
        assert patch["target_anchor"] == finding["translation_anchor"]


class TypedCorrectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.slug = "typed-book"
        self.book = self.root / "books" / self.slug
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "translated").mkdir()
        src = self.book / "extracted" / "001.md"
        dst = self.book / "translated" / "001.md"
        src.write_text("Test source text for reviewed corrections.", encoding="utf-8")
        dst.write_text("wrong-1 wrong-2 wrong-3 wrong-4", encoding="utf-8")
        (self.book / "glossary.md").write_text("Test glossary", encoding="utf-8")
        (self.book / "style-guide.md").write_text("Test style", encoding="utf-8")
        self.chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "status": "translated", "source_path": "extracted/001.md",
            "translation_path": "translated/001.md",
            "translation_acceptance": {
                "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "translation_sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
            },
        }
        self.findings = [{
            "id": f"meaning-{i}", "category": "meaning", "resolved": False,
            "translation_anchor": {"quote": f"wrong-{i}", "occurrence": 0},
        } for i in range(1, 5)]
        self.review = {
            "record_id": "a" * 32, "outcome": "CORRECTIONS_REQUIRED",
            **self.chapter["translation_acceptance"],
            "quality_report": {"evidence_version": 2,
                               "findings": self.findings},
        }
        self.status = {
            "state": "running", "workflow_revision": "b" * 40,
            "model_calls_started": 0,
            "active": {"role": "corrector", "chapter": 1, "session_id": "test"},
        }

    def context(self, engine, slug, chapter, role, *, correction_findings):
        return "system", json.dumps([f["id"] for f in correction_findings]), "source"

    def test_oversized_response_recovers_with_only_one_focused_finding(self):
        calls = []
        def model(role, system, user):
            ids = json.loads(user.split("\nREJECTED RESPONSE:")[0])
            calls.append(ids)
            if len(calls) == 1:
                return json.dumps({"patches": [{
                    "finding_id": ids[0], "operation": "replace",
                    "replacement": "z" * 10_001,
                }], "glossary": [], "style_guide": []})
            return json.dumps({
                "patches": [{"finding_id": i, "operation": "replace",
                             "replacement": "fixed-" + i.split("-")[1]}
                            for i in ids],
                "glossary": [], "style_guide": [],
            })
        with patch.object(worker, "ROOT", self.root), \
             patch.object(worker, "context", side_effect=self.context), \
             patch.object(worker, "role_contracts",
                          return_value=("translator", ["AGENTS.md"])), \
             patch.object(worker, "complete", side_effect=model), \
             patch.object(worker, "load_public_correction_helper",
                          return_value=_Protocol), \
             patch.object(worker, "checkpoint") as checkpoint:
            result = worker.process_correction_batches(
                "c" * 32, self.slug, self.status,
                self.root, self.chapter, self.review)
        self.assertIsNotNone(result)
        self.assertEqual(calls[0], ["meaning-1", "meaning-2", "meaning-3"])
        self.assertEqual(calls[1], ["meaning-1"])
        self.assertEqual(calls[2], ["meaning-2", "meaning-3", "meaning-4"])
        self.assertEqual(result[0], "fixed-1 fixed-2 fixed-3 fixed-4")
        self.assertEqual(self.status["model_calls_started"], 3)
        self.assertEqual(checkpoint.call_count, 5)
        self.assertEqual(
            (self.book / self.chapter["translation_path"]).read_text("utf-8"),
            "wrong-1 wrong-2 wrong-3 wrong-4",
            "canonical acceptance, not the model helper, owns book writes")

    def test_transport_failure_does_not_trigger_protocol_repair(self):
        with patch.object(worker, "ROOT", self.root), \
             patch.object(worker, "context", side_effect=self.context), \
             patch.object(worker, "role_contracts",
                          return_value=("translator", ["AGENTS.md"])), \
             patch.object(worker, "complete",
                          side_effect=ModelError("model transport timeout or connection failure")) as model, \
             patch.object(worker, "load_public_correction_helper",
                          return_value=_Protocol), \
             patch.object(worker, "checkpoint"):
            with self.assertRaisesRegex(ModelError, "transport"):
                worker.process_correction_batches(
                    "c" * 32, self.slug, self.status,
                    self.root, self.chapter, self.review)
        model.assert_called_once()

    def test_reviewer_hash_bound_deferral_not_success(self):
        status = {"failures": 3, "state": "blocked", "phase": "model-blocked"}
        with patch.object(worker, "cli", return_value="ok") as cli:
            self.assertTrue(worker.defer_protocol_failed_unit(
                self.slug, self.chapter, self.review, status, "invalid_replacement"))
        cli.assert_called_once_with(
            "defer-review", self.slug, "1", "--reason", "operator-attention")
        self.assertEqual((status["state"], status["phase"]),
                         ("running", "model-unit-deferred"))
        self.assertEqual(status["model_unit_deferral_audit"][0]["review_record_id"],
                         self.review["record_id"])
        self.assertNotIn("PASS", str(status))
        stale = {**self.review, "translation_sha256": "d" * 64}
        with patch.object(worker, "cli") as cli:
            with self.assertRaisesRegex(worker.JobError, "current reviewer evidence"):
                worker.defer_protocol_failed_unit(
                    self.slug, self.chapter, stale, {"failures": 3},
                    "invalid_replacement")
            cli.assert_not_called()
        with patch.object(worker, "cli") as cli:
            self.assertFalse(worker.defer_protocol_failed_unit(
                self.slug, self.chapter, self.review, {"failures": 2}, "invalid"))
            self.assertFalse(worker.defer_protocol_failed_unit(
                self.slug, self.chapter, self.review, {"failures": 3}, None))
            cli.assert_not_called()

    def test_runner_surfaces_blocked_checkpoint_to_actions(self):
        target = self.root / "actions-output"
        with patch.dict("os.environ", {"GITHUB_OUTPUT": str(target)}):
            worker.publish_outputs("c" * 32, {"state": "blocked", "phase": "model-blocked"})
        self.assertIn("blocked=true", target.read_text("utf-8"))
        self.assertIn("continue_queue=true", target.read_text("utf-8"))

    def test_protocol_parser_distinguishes_empty_insertion_and_too_large(self):
        finding = {"id": "omission-1", "resolved": False,
                   "translation_anchor": None}
        def payload(value):
            return json.dumps({"patches": [{
                "finding_id": "omission-1", "operation": "insert_after",
                "insertion_anchor": {"quote": "nearby", "occurrence": 0},
                "replacement": value,
            }], "glossary": [], "style_guide": []})
        with self.assertRaisesRegex(ModelError, "nonempty"):
            worker.parse_correction_patches(payload(""), [finding])
        with self.assertRaisesRegex(ModelError, "maximum length"):
            worker.parse_correction_patches(payload("x" * 10_001), [finding])


if __name__ == "__main__":
    unittest.main()
