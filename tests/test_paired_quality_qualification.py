import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import paired_quality_qualification as pair


class PairProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.target = "Четыре пуговицы."
        self.hashes = {"source_sha256": "a" * 64,
                       "translation_sha256": pair.export.sha(self.target.encode("utf-8"))}
        self.chapters = [{"number": 1, "unit_id": "chapter-000001",
                         "translation_acceptance": dict(self.hashes, session_id="chat-translator")}]
        self.report = {"evidence_version": 2, "context": "independent_session",
                       "checks": dict.fromkeys(("coverage", "meaning", "terminology",
                                                "style", "structure"), True), "findings": []}
        self.records = [dict(self.hashes, unit_id="chapter-000001", sequence=1,
                             outcome="PASS", reviewer_session_id="chat-reviewer",
                             quality_report=copy.deepcopy(self.report))]
        self.requests = [dict(self.hashes, chapter=1, session_id="chat-translator",
                              operation="accept-translation", translation=self.target),
                         dict(self.hashes, chapter=1, session_id="chat-reviewer",
                              operation="review-record", outcome="PASS",
                              quality_report=copy.deepcopy(self.report))]

    def verify(self):
        pair.verify_receipts(self.chapters, self.records, self.requests)

    def test_matching_canonical_receipts(self):
        self.verify()

    def test_reject_wrong_bytes_or_missing_receipt(self):
        self.requests[0]["translation"] = "Изменено"
        with self.assertRaisesRegex(ValueError, "translation receipt"):
            self.verify()
        self.requests[0]["translation"] = self.target
        self.requests.pop()
        with self.assertRaisesRegex(ValueError, "PASS receipt"):
            self.verify()

    def test_reject_same_session_or_superseding_rejection(self):
        self.records[0]["reviewer_session_id"] = "chat-translator"
        with self.assertRaises(ValueError):
            self.verify()
        self.records[0]["reviewer_session_id"] = "chat-reviewer"
        self.records.append(dict(self.records[0], sequence=2, outcome="CORRECTIONS_REQUIRED"))
        with self.assertRaises(ValueError):
            self.verify()

    def test_preflight_failure_prevents_auto_calls(self):
        args = Mock(executor_revision="a" * 40)
        report = {}
        with patch.object(pair.export, "git", return_value="a" * 40), \
             patch.object(pair, "admit_chatops", side_effect=ValueError("not ready")), \
             patch.object(pair.qualification, "step") as step:
            with self.assertRaises(ValueError):
                pair.run(args, report)
        step.assert_not_called()
        self.assertEqual(report["stage"], "chatops-preflight")


    def test_bounded_or_missing_context_cannot_claim_independent_review(self):
        for context in ("bounded_context", None):
            with self.subTest(context=context):
                self.records[0]["quality_report"]["context"] = context
                self.requests[1]["quality_report"]["context"] = context
                with self.assertRaisesRegex(ValueError, "independent ChatOps review context"):
                    self.verify()

    def test_receipt_must_match_current_canonical_report(self):
        self.requests[1]["quality_report"]["checks"]["style"] = False
        with self.assertRaisesRegex(ValueError, "PASS receipt"):
            self.verify()


class FailedPairedDiagnosticsTests(unittest.TestCase):
    def test_exact_fixture_only(self):
        with patch.object(pair.export, "clean_head") as clean:
            with self.assertRaisesRegex(ValueError, "benchmark identity mismatch"):
                pair.admit_chatops(Path("."), "a" * 40, "b" * 32, "c" * 64)
            clean.assert_not_called()

    def test_failed_auto_evidence_is_bound_to_current_record(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "books" / "fixture"
            book.mkdir(parents=True)
            (book / "s").write_text("Source.", encoding="utf-8")
            (book / "t").write_text("Перевод.", encoding="utf-8")
            hashes = {"source_sha256": pair.export.sha((book / "s").read_bytes()),
                      "translation_sha256": pair.export.sha((book / "t").read_bytes())}
            unit = "chapter-000001"
            record = "a" * 32
            payloads = {
                "metadata.json": {"source": {"sha256": pair.FIXTURE_SHA256}},
                "progress.json": {"chapters": [{"unit_id": unit, "status": "translated",
                    "source_path": "s", "translation_path": "t", "translation_acceptance": hashes}]},
                "review-ledger.json": {"records": [dict(hashes, unit_id=unit, record_id=record,
                    sequence=1, outcome="CORRECTIONS_REQUIRED", quality_report={"findings": [
                        {"id": "f", "category": "meaning", "severity": "major", "detail": "Wrong meaning",
                         "resolved": False}]})]}}
            for name, value in payloads.items():
                (book / name).write_text(json.dumps(value), encoding="utf-8")
            status = {"book_slug": "fixture", "correction_regression_rejections": [
                {"unit_id": unit, "review_record_id": record, "finding_id": "f",
                 "reason": "quality-candidate-veto", "risk": "meaning", "patch_sha256": "b"*64},
                {"unit_id": unit, "review_record_id": "c"*32, "finding_id": "f",
                 "reason": "unrelated"}]}
            report = {}
            pair.failed_auto_evidence(root, status, report)
            self.assertEqual(report["auto_reviewed_units"], 0)
            evidence = report["auto_units"][0]
            self.assertEqual(evidence["findings"][0]["detail"], "Wrong meaning")
            self.assertEqual(len(evidence["candidate_rejections"]), 1)
            self.assertEqual(evidence["candidate_rejections"][0]["risk"], "meaning")
            (book / "t").write_text("Changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "accepted bytes mismatch"):
                pair.failed_auto_evidence(root, status, {})


class CompletedComparisonDiagnosticsTests(unittest.TestCase):
    def test_nonpassing_samples_preserve_exact_hashes_without_extra_calls(self):
        source = "He said no."
        auto = "Он сказал нет. synthetic-secret"
        chatops = "Он ответил: нет."
        def artifact(text):
            return {"text": text, "sha256": pair.export.sha(text.encode("utf-8"))}
        original = artifact(source)
        left, right = artifact(auto), artifact(chatops)
        left["source_sha256"] = right["source_sha256"] = original["sha256"]
        unit = {"unit_id": "chapter-000001", "source": original, "auto": left,
                "chatops": right}
        manifest = {"schema_version": 1, "target_language": "ru", "units": [unit]}
        checks = dict.fromkeys(pair.CRITERIA, "pass")
        row = {"unit_id": unit["unit_id"], "source_sha256": original["sha256"],
               "candidate_sha256": {"auto": left["sha256"], "chatops": right["sha256"]},
               "consensus": {"auto": dict(checks, dialogue="fail"), "chatops": checks}}
        result = {"state": "complete", "units": [row]}
        report = {}
        with patch.dict(pair.os.environ, {"OMNIROUTE_API_KEY": "synthetic-secret"}):
            pair.comparison_diagnostics(manifest, result, pair.FIXTURE_SHA256, report)
        self.assertFalse(report["all_criteria_pass_both_modes"])
        self.assertFalse(report["quality_equivalence_established"])
        evidence = report["synthetic_comparison_samples"][0]["artifacts"]["auto"]
        self.assertEqual(evidence["original_sha256"], left["sha256"])
        self.assertTrue(evidence["redacted"])
        self.assertNotIn("synthetic-secret", evidence["excerpt"])
        row["consensus"]["auto"]["dialogue"] = "pass"
        pair.comparison_diagnostics(manifest, result, pair.FIXTURE_SHA256, report)
        self.assertTrue(report["all_criteria_pass_both_modes"])
        self.assertEqual(report["synthetic_comparison_samples"], [])
        self.assertFalse(report["quality_equivalence_established"])
        with self.assertRaisesRegex(ValueError, "fixture identity"):
            pair.comparison_diagnostics(manifest, result, "b"*64, {})
        row["candidate_sha256"]["auto"] = "0"*64
        with self.assertRaisesRegex(ValueError, "artifact mismatch"):
            pair.comparison_diagnostics(manifest, result, pair.FIXTURE_SHA256, {})
