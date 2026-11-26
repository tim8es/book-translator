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
