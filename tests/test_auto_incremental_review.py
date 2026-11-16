"""Synthetic incremental reviewer evidence/rebase regressions.

No protected books or provider calls; opt-in v2 checkpoints are independently
hash-bound while old v1 checkpoints remain unchanged.
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
import book_runner as runner
import incremental_review as incr
import review_chunks as chunks


def texts():
    source, target = [], []
    for i in range(36):
        marker = f"<!-- bt-block:{i + 1:06d} -->\n"
        source.append(marker + f"Source unique {i:04d}. " +
                      "The river flowed quietly beside the hill. " * 30 + "\n\n")
        target.append(marker + f"Перевод уникальный {i:04d}. " +
                      "Река текла тихо возле холма. " * 40 + "\n\n")
    return "".join(source), "".join(target)


def checked_part(outcome="PASS", findings=None):
    payload = {
        "evidence_version": 2, "outcome": outcome,
        "checks": dict.fromkeys(
            ("coverage", "meaning", "terminology", "style", "structure"), True),
        "findings": findings or [], "style_guide": [],
    }
    accepted, report, style = runner.parse_literary_review(
        json.dumps(payload, ensure_ascii=False))
    return {"outcome": accepted, "report": report, "style": style, "glossary": []}


class IncrementalReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.source, self.target = texts()
        self.plan = chunks.plan_aligned(self.source, self.target)
        self.assertGreaterEqual(len(self.plan["chunks"]), 4)
        self.unit = "chapter-000001"
        self.revision = "f" * 40
        self.binding = incr.binding(None, "a" * 64, "b" * 64, "c" * 64)
        self.stage = incr.new_stage(
            self.plan, self.unit, self.revision, self.binding)

    def add(self, index, part=None):
        current = self.plan["chunks"][index]
        self.stage["completed"][current["id"]] = part or checked_part()
        incr.record_chunk(self.stage, current, self.source, self.target)

    def change_middle(self):
        middle = self.plan["chunks"][len(self.plan["chunks"]) // 2]
        left, right = middle["translation_core"]
        position = self.target.find("Река", (left + right) // 2, right)
        self.assertGreater(position, 0)
        updated = self.target[:position] + "Море" + self.target[position + 4:]
        newer_plan = chunks.plan_aligned(self.source, updated)
        return middle, updated, newer_plan

    def test_v2_reuses_unchanged_clean_partial_review_and_rechecks_modified(self):
        for i in range(len(self.plan["chunks"])):
            self.add(i)
        middle, revised, new_plan = self.change_middle()
        resumed, reused = incr.rebase(
            self.stage, new_plan, self.source, revised, self.binding,
            None, self.unit, self.revision)
        self.assertGreater(reused, 0)
        self.assertLess(reused, len(self.plan["chunks"]))
        self.assertNotIn(middle["id"], resumed["completed"])
        self.assertIsNone(resumed["accepted_review_record_id"])
        incr.validate_current(
            resumed, new_plan, self.source, revised, self.binding,
            self.unit, self.revision)
        self.assertEqual(set(resumed["completed"]), set(resumed["reused"]))

    def test_changed_glossary_or_review_contract_disables_reuse(self):
        for i in range(len(self.plan["chunks"])):
            self.add(i)
        _, revised, newer = self.change_middle()
        different = incr.binding(None, "0"*64, "b"*64, "c"*64)
        resumed, count = incr.rebase(
            self.stage, newer, self.source, revised,
            different, None, self.unit, self.revision)
        self.assertEqual(count, 0)
        self.assertEqual(resumed["completed"], {})

    def test_legacy_and_malformed_checkpoint_not_reinterpreted_as_v2(self):
        old = chunks.new_stage(self.plan, self.unit, self.revision)
        self.assertEqual(old["schema_version"], 1)
        with self.assertRaisesRegex(ValueError, "invalid"):
            incr.validate_shape(old, self.unit, self.revision)
        self.add(0)
        bad = json.loads(json.dumps(self.stage))
        bad["windows"][self.plan["chunks"][0]["id"]]["translation_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "window hash"):
            incr.validate_current(
                bad, self.plan, self.source, self.target, self.binding,
                self.unit, self.revision)
        bad = json.loads(json.dumps(self.stage))
        bad["reused"]["chunk-099"] = {
            "from_translation_sha256": "0" * 64, "from_record_id": None}
        with self.assertRaises(ValueError):
            incr.validate_shape(bad, self.unit, self.revision)

    def test_incomplete_old_review_with_changed_previous_record_cannot_reuse(self):
        self.add(0)
        _, revised, new_plan = self.change_middle()
        newer_binding = incr.binding("new-review-record", "a"*64, "b"*64, "c"*64)
        resumed, count = incr.rebase(
            self.stage, new_plan, self.source, revised, newer_binding,
            None, self.unit, self.revision)
        self.assertEqual(count, 0)
        self.assertFalse(resumed["reused"])

    def test_previous_unresolved_finding_forces_new_review_of_same_clean_chunk(self):
        self.add(0)
        _, revised, new_plan = self.change_middle()
        source_quote = "Source unique 0000."
        finding = {
            "id": "a", "category": "meaning", "severity": "major",
            "detail": "Needs a fresh source comparison", "resolved": False,
            "source_anchor": {"quote": source_quote, "occurrence": 0},
            "translation_anchor": None, "resolves": None,
        }
        prior = {"record_id": "old-1",
                 "quality_report": {"evidence_version": 2, "findings": [finding]}}
        self.stage["binding"]["previous_record_id"] = "old-1"
        current_binding = incr.binding("old-1", "a"*64, "b"*64, "c"*64)
        resumed, count = incr.rebase(
            self.stage, new_plan, self.source, revised, current_binding,
            prior, self.unit, self.revision)
        self.assertNotIn("chunk-001", resumed["completed"])
        self.assertEqual(count, 0)

    def test_accepted_rejected_review_requires_matching_canonical_record(self):
        # A recorded, fully independently reviewed previous revision is
        # eligible only when the ledger exactly binds it to these chunks.
        middle = self.plan["chunks"][len(self.plan["chunks"]) // 2]
        quote = next(q for q in ("Source unique 0012.", "Source unique 0022.",
                                 "Source unique 0018.") if self.source.find(q)
                    in range(*middle["source_core"]))
        target_quote = quote.replace("Source unique", "Перевод уникальный")
        finding = {
            "id": "issue-1", "category": "meaning", "severity": "major",
            "detail": "Synthetic semantic difference", "resolved": False,
            "source_anchor": {"quote": quote, "occurrence": 0},
            "translation_anchor": {"quote": target_quote, "occurrence": 0},
            "resolves": None,
        }
        for i, chunk in enumerate(self.plan["chunks"]):
            self.add(i, checked_part(
                "CORRECTIONS_REQUIRED", [finding]) if chunk["id"] == middle["id"]
                else checked_part())
        combined = chunks.combine(self.stage)
        previous = {
            "record_id": "record-accepted", "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": self.plan["source_sha256"],
            "translation_sha256": self.plan["translation_sha256"],
            "quality_report": {"evidence_version": 2,
                               "checks": combined["checks"],
                               "findings": combined["findings"]},
        }
        self.stage["accepted_review_record_id"] = previous["record_id"]
        new_binding = incr.binding(
            previous["record_id"], "a"*64, "b"*64, "c"*64)
        _, revised, plan = self.change_middle()
        retained, count = incr.rebase(
            self.stage, plan, self.source, revised, new_binding,
            previous, self.unit, self.revision)
        self.assertGreater(count, 0)
        self.assertNotIn(middle["id"], retained["completed"])
        wrong = {**previous, "record_id": "different"}
        restarted, count = incr.rebase(
            self.stage, plan, self.source, revised, new_binding,
            wrong, self.unit, self.revision)
        self.assertEqual(count, 0)
        self.assertFalse(restarted["completed"])

    def test_archive_matches_record_and_does_not_persist_unverified_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "synthetic"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            (book / "extracted/unit.md").write_text(self.source, "utf-8")
            (book / "translated/unit.md").write_text(self.target, "utf-8")
            chapter = {
                "unit_id": self.unit, "source_path": "extracted/unit.md",
                "translation_path": "translated/unit.md",
            }
            for i in range(len(self.plan["chunks"])):
                self.add(i)
            # One reviewer finding makes this a rejected, not a PASS review.
            part = self.plan["chunks"][2]
            target_quote = "Перевод уникальный 0018."
            source_quote = "Source unique 0018."
            if not (part["source_core"][0] <= self.source.find(source_quote)
                    < part["source_core"][1]):
                source_quote = next(
                    f"Source unique {i:04d}."
                    for i in range(36)
                    if part["source_core"][0] <= self.source.find(
                        f"Source unique {i:04d}.") < part["source_core"][1])
                target_quote = source_quote.replace(
                    "Source unique", "Перевод уникальный")
            finding = {
                "id": "fix-1", "category": "meaning", "severity": "major",
                "detail": "Synthetic mismatch", "resolved": False,
                "source_anchor": {"quote": source_quote, "occurrence": 0},
                "translation_anchor": {"quote": target_quote, "occurrence": 0},
                "resolves": None,
            }
            self.stage["completed"][part["id"]] = checked_part(
                "CORRECTIONS_REQUIRED", [finding])
            combined = chunks.combine(self.stage)
            record = {
                "record_id": "record-1", "outcome": "CORRECTIONS_REQUIRED",
                "source_sha256": self.plan["source_sha256"],
                "translation_sha256": self.plan["translation_sha256"],
                "quality_report": {"evidence_version": 2,
                                   "checks": combined["checks"],
                                   "findings": combined["findings"]},
            }
            path = runner.review_chunk_stage_path("synthetic", self.unit)
            status = {"workflow_revision": self.revision}
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "chapter_data",
                              return_value=({}, {"records": [record]}, [chapter])), \
                 patch.object(runner, "latest_record", return_value=record):
                runner.store_json(path, self.stage)
                self.assertTrue(runner.archive_incremental_reviewer_stage(
                    "synthetic", chapter, status, record["quality_report"]))
                self.assertEqual(runner.read_json(path)["accepted_review_record_id"],
                                 "record-1")
                wrong = {**record, "translation_sha256": "0"*64}
                with patch.object(runner, "latest_record", return_value=wrong):
                    self.assertFalse(runner.archive_incremental_reviewer_stage(
                        "synthetic", chapter, status, record["quality_report"]))


class IncrementalWorkerTests(unittest.TestCase):
    def test_resumed_review_skips_only_verified_clean_window_model_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books/synthetic"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            source, target = texts()
            (book / "extracted/unit.md").write_text(source, "utf-8")
            (book / "translated/unit.md").write_text(target, "utf-8")
            (book / "glossary.md").write_text("", "utf-8")
            (book / "style-guide.md").write_text("", "utf-8")
            (book / "metadata.json").write_text(json.dumps({
                "title": "Synthetic", "author": "Test",
                "source_language": "en", "target_language": "ru"}), "utf-8")
            plan = chunks.plan_aligned(source, target)
            unit, revision = "chapter-000001", "a"*40
            chapter = {"unit_id": unit, "number": 1, "status": "translated",
                       "source_path": "extracted/unit.md",
                       "translation_path": "translated/unit.md"}
            from hashlib import sha256
            memo = sha256(b"").hexdigest()
            bound = incr.binding(None, memo, memo, chunks.sha("Pinned review"))
            stage = incr.new_stage(plan, unit, revision, bound)
            for part in plan["chunks"]:
                stage["completed"][part["id"]] = checked_part()
                incr.record_chunk(stage, part, source, target)
            path = book / ".workflow/auto-review-chunks/chapter-000001.json"
            runner.store_json(path, stage)
            chosen = plan["chunks"][len(plan["chunks"]) // 2]
            left, right = chosen["translation_core"]
            position = target.find("Река", (left + right) // 2, right)
            amended = target[:position] + "Море" + target[position + 4:]
            (book / "translated/unit.md").write_text(amended, "utf-8")
            status = {
                "workflow_revision": revision, "model_calls_started": 0,
                "active": {"unit_id": unit, "chapter": 1,
                           "role": "reviewer", "session_id": "synthetic-review"},
            }
            payload = json.dumps({
                "evidence_version": 2, "outcome": "PASS",
                "checks": dict.fromkeys(
                    ("coverage", "meaning", "terminology", "style", "structure"), True),
                "findings": [], "style_guide": [],
            })
            with patch.dict(os.environ, {"BOOK_TRANSLATOR_INCREMENTAL_REVIEW": "on"}), \
                 patch.object(runner, "ROOT", root), \
                 patch.object(runner, "role_contracts",
                              return_value=("Pinned review", ["AGENTS.md"])), \
                 patch.object(runner, "full_shared_context", return_value=("", "")), \
                 patch.object(runner, "model_glossary_context", return_value=""), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "complete_for_job",
                              return_value=payload) as complete:
                self.assertIsNone(runner.process_review_chunks(
                    "b"*32, "synthetic", status, root, chapter,
                    previous=None, owned={"session_id": "synthetic-review"}))
            complete.assert_called_once()
            self.assertEqual(status["model_calls_started"], 1)
            self.assertGreater(status["review_incremental_rebases"][-1][
                "reused_clean_chunks"], 0)
            self.assertLess(status["review_incremental_rebases"][-1][
                "reused_clean_chunks"], len(plan["chunks"]))
            saved = runner.read_json(path)
            incr.validate_shape(saved, unit, revision)
            self.assertEqual(len(saved["completed"]),
                             status["review_incremental_rebases"][-1][
                                 "reused_clean_chunks"] + 1)


if __name__ == "__main__":
    unittest.main()
