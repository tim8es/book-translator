"""Offline checks of the real preserved EPUB source and mandatory planning evidence."""
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "scripts")]
import long_agent_qualification as long
from workflow import epub_preserve
import review_chunks


@dataclass
class Chapter:
    title: str
    content: str


class LongAgentFixtureTests(unittest.TestCase):
    def test_real_preserved_extraction_admits_optional_planning_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.epub"
            long.fixture(path)
            self.assertTrue(epub_preserve.needs_preserved_xhtml(path))
            chapters = epub_preserve.extract(path, Chapter)
            self.assertEqual(len(chapters), 1)
            source = chapters[0].content
            self.assertGreaterEqual(long.check_source(source), len(long.PLACES))
            for i in range(1, 27):
                self.assertIn(f"entry {i} in the harbour ledger", source)
            plan = review_chunks.plan_aligned(source, source, stable=True)
            self.assertEqual(plan["helper_revision"], review_chunks.STABLE_ALIGNMENT_REVISION)
            self.assertTrue(2 <= len(plan["chunks"]) <= 4)

    def test_completion_without_actual_current_advice_is_not_qualification(self):
        unit = "chapter-000001"
        source, target = "Source", "Перевод"
        hashes = {"source_sha256": review_chunks.sha(source),
                  "translation_sha256": review_chunks.sha(target)}
        chapter = {"unit_id": unit, "translation_acceptance": hashes}
        records = [dict(hashes, unit_id=unit, outcome="PASS", sequence=1)]
        status = {"workflow_revision": "a"*40}
        with self.assertRaisesRegex(RuntimeError, "never exercised"):
            long.final_decision(status, chapter, records, source, target)
        decision = {"binding": dict(hashes, workflow_revision=status["workflow_revision"]),
                    "outcome": "accepted", "strategy": "full_direct", "chunk_count": 2}
        status["review_orchestrator_decisions"] = {unit: decision}
        self.assertEqual(long.final_decision(status, chapter, records, source, target)["strategy"],
                         "full_direct")
        decision["outcome"] = "fallback"
        with self.assertRaisesRegex(RuntimeError, "evidence missing"):
            long.final_decision(status, chapter, records, source, target)
        decision["outcome"] = "accepted"
        with self.assertRaisesRegex(RuntimeError, "evidence missing"):
            long.final_decision(status, chapter, records, source, target + "changed")


    def test_failure_evidence_is_current_hash_bound_and_redacted(self):
        from unittest.mock import patch
        source, target = "Source sentence", "Перевод предложения"
        unit = "chapter-000001"
        hashes = {"source_sha256": review_chunks.sha(source),
                  "translation_sha256": review_chunks.sha(target)}
        chapter = {"unit_id": unit, "translation_acceptance": hashes}
        finding = {"id": "meaning-1", "category": "meaning", "severity": "major",
                   "resolved": False, "source_anchor": {"quote": source, "occurrence": 0},
                   "translation_anchor": {"quote": target, "occurrence": 0},
                   "detail": "bad meaning private-sentinel"}
        record = {**hashes, "unit_id": unit, "record_id": "a"*32, "sequence": 2,
                  "outcome": "CORRECTIONS_REQUIRED", "quality_report": {"findings": [finding]}}
        stale = {**record, "sequence": 1, "record_id": "b"*32,
                 "translation_sha256": "f"*64}
        status = {"workflow_revision": "d"*40, "review_orchestrator_decisions": {
            unit: {"binding": {**hashes, "workflow_revision": "d"*40},
                   "outcome": "accepted", "strategy": "aligned_chunks", "chunk_count": 2}},
            "correction_regression_rejections": [
                {"unit_id": unit, "review_record_id": "a"*32, "finding_id": "meaning-1",
                 "reason": "veto", "patch_sha256": "c"*64},
                {"unit_id": unit, "review_record_id": "b"*32, "finding_id": "meaning-1",
                 "reason": "stale-sentinel"}]}
        with patch.dict("os.environ", {"OMNIROUTE_API_KEY": "private-sentinel"}):
            result = long.failed_evidence(status, chapter, [record, stale], source, target)
        self.assertEqual(result["current_review_record_id"], "a"*32)
        self.assertEqual(len(result["review_history"]), 2)
        self.assertTrue(result["agent_advice"]["current_binding"])
        self.assertEqual(result["findings"][0]["source_anchor"]["quote"], source)
        self.assertEqual(len(result["candidate_rejections"]), 1)
        self.assertNotIn("private-sentinel", str(result))
        self.assertNotIn("stale-sentinel", str(result))
        with self.assertRaisesRegex(ValueError, "accepted bytes mismatch"):
            long.failed_evidence(status, chapter, [record], source, target + " changed")
        finding["source_anchor"]["quote"] = "unverified-sentinel"
        result = long.failed_evidence(status, chapter, [record], source, target)
        self.assertEqual(result["unreported_findings"], 1)
        self.assertEqual(result["findings"], [])
        self.assertNotIn("unverified-sentinel", str(result))

    def test_failure_evidence_does_not_treat_stale_review_as_current(self):
        source, target = "Source", "Target"
        hashes = {"source_sha256": review_chunks.sha(source),
                  "translation_sha256": review_chunks.sha(target)}
        chapter = {"unit_id": "chapter-000001", "translation_acceptance": hashes}
        record = {**hashes, "translation_sha256": "f"*64,
                  "unit_id": chapter["unit_id"], "record_id": "a"*32,
                  "sequence": 1, "outcome": "CORRECTIONS_REQUIRED"}
        row = long.failed_evidence({}, chapter, [record], source, target)
        self.assertEqual(len(row["review_history"]), 1)
        self.assertNotIn("findings", row)
        self.assertNotIn("current_review_record_id", row)


    def test_anchored_audit_records_negative_without_extra_calls(self):
        import json
        from unittest.mock import patch
        source, target = "The light on the water.", "Свет на воде."
        report, calls = {}, []
        def complete(*args):
            calls.append(args)
            return json.dumps({"checks": {"coverage": True, "literary_style": False},
                "findings": [{"check": "literary_style", "category": "style",
                    "source_quote": source, "target_quote": target,
                    "detail": "Concrete synthetic example private-sentinel"}]})
        with patch.dict("os.environ", {"OMNIROUTE_API_KEY": "private-sentinel"}):
            result = long.anchored_audit(complete, {"coverage": "complete",
                "literary_style": "idiomatic"}, source, target, report)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["literary_style"])
        self.assertEqual(report["audit_evidence"]["translation_sha256"], review_chunks.sha(target))
        self.assertEqual(report["audit_evidence"]["findings"][0]["target_quote"], target)
        self.assertNotIn("private-sentinel", str(report))

    def test_invalid_negative_evidence_cannot_pass(self):
        import json
        criteria = {"style": "idiomatic"}
        good = {"check": "style", "category": "style", "source_quote": "Source",
                "target_quote": "Target", "detail": "Concrete issue"}
        for checks, findings in [({"style": False}, []),
                                 ({"style": False}, [{**good, "target_quote": "Invented"}]),
                                 ({"style": True}, [good])]:
            report = {}
            with self.assertRaisesRegex(RuntimeError, "anchored audit evidence"):
                long.anchored_audit(lambda *args: json.dumps({"checks": checks,
                    "findings": findings}), criteria, "Source", "Target", report)
            self.assertEqual(report["independent_audit"], checks)
            self.assertNotIn("audit_evidence", report)

    def test_all_true_requires_empty_evidence(self):
        report = {}
        self.assertEqual(long.anchored_audit(lambda *args:
            '{"checks":{"style":true},"findings":[]}', {"style": "idiomatic"},
            "Source", "Target", report), {"style": True})
        self.assertEqual(report["audit_evidence"]["findings"], [])
