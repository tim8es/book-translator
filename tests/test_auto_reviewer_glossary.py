"""Regression tests for model-authored glossary memory and independent review.

Only synthetic fixtures; no copyrighted book text, network, or model API.
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
sys.path.insert(0, str(ROOT / "scripts"))

import book_runner as runner
import review_chunks
from literary_context import (
    parse_literary_review, reviewer_glossary_proposals,
)
from omniroute import ModelError


def reviewer_payload(glossary=None, style=None):
    result = {
        "evidence_version": 2, "outcome": "PASS",
        "checks": dict.fromkeys(
            ("coverage", "meaning", "terminology", "style", "structure"), True
        ),
        "findings": [], "style_guide": style or [],
    }
    if glossary is not None:
        result["glossary"] = glossary
    return json.dumps(result, ensure_ascii=False)


def name(original="Mara", translation="Мара"):
    return {"original": original, "translation": translation,
            "type": "person", "notes": "Verified recurring character"}


def style_entry():
    return {"source_pattern": "clipped replies",
            "translation_approach": "retain brief sentences",
            "evidence": "accepted passage"}


class ReviewerGlossaryTests(unittest.TestCase):
    def test_parser_preserves_legacy_shape_and_validates_optional_glossary(self):
        self.assertEqual(reviewer_glossary_proposals(reviewer_payload()), [])
        legacy = parse_literary_review(reviewer_payload())
        self.assertEqual(legacy[0], "PASS")
        glossary = [name()]
        current = reviewer_payload(glossary)
        self.assertEqual(parse_literary_review(current)[0], "PASS")
        self.assertEqual(reviewer_glossary_proposals(current), glossary)
        invalid = json.loads(current)
        invalid["glossary"] = [{"original": "Mara", "translation": "Мара"}]
        with self.assertRaises(ModelError):
            parse_literary_review(json.dumps(invalid, ensure_ascii=False))
        with self.assertRaises(ModelError):
            reviewer_glossary_proposals(json.dumps(invalid, ensure_ascii=False))

    def test_empty_notes_valid_for_reviewer(self):
        row = {**name(), "notes": ""}
        payload = reviewer_payload([row])
        self.assertEqual(reviewer_glossary_proposals(payload), [row])
        self.assertEqual(parse_literary_review(payload)[0], "PASS")
        with self.assertRaises(ModelError):
            reviewer_glossary_proposals(reviewer_payload([{**row, "type": ""}]))
        with self.assertRaises(ModelError):
            reviewer_glossary_proposals(reviewer_payload([{**row, "notes": None}]))

    def test_chunked_review_aggregates_glossary_and_accepts_legacy_stage(self):
        source = ("Alpha paragraph.\n\n" * 90)
        target = ("Альфа-параграф.\n\n" * 90)
        plan = review_chunks.plan(source, target)
        stage = review_chunks.new_stage(plan, "chapter-000001", "a" * 40)
        report = {"evidence_version": 2, "context": "independent_session",
                  "checks": dict.fromkeys(
                      ("coverage", "meaning", "terminology", "style", "structure"), True),
                  "findings": []}
        for i, part in enumerate(plan["chunks"]):
            entry = {"outcome": "PASS", "report": report, "style": []}
            if i == 0:
                entry["glossary"] = [name()]
            stage["completed"][part["id"]] = entry
        review_chunks.validate_stage(stage, plan, "chapter-000001", "a" * 40)
        combined = review_chunks.combine(stage)
        self.assertEqual(reviewer_glossary_proposals(json.dumps(combined)), [name()])
        self.assertEqual(parse_literary_review(json.dumps(combined))[0], "PASS")

    def test_reviewer_glossary_saves_via_one_canonical_target_per_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            book = repo / "books" / "sample"
            for sub in ("extracted", "translated", ".workflow/claims"):
                (book / sub).mkdir(parents=True, exist_ok=True)
            # A linked tag would previously be misclassified and saved by code.
            # Only the explicitly proposed model entry may be persisted.
            source = ("<!-- bt-block:000001 -->\nMara walks. Theo waits.\n"
                      "<!-- bt-inline:000001:start -->Winter Festival<!-- bt-inline:000001:end -->")
            translation = ("<!-- bt-block:000001 -->\nМара идёт. Тео ждёт.\n"
                           "<!-- bt-inline:000001:start -->Зимний Фестиваль<!-- bt-inline:000001:end -->")
            (book / "extracted" / "001.md").write_text(source, "utf-8")
            (book / "translated" / "001.md").write_text(translation, "utf-8")
            (book / "glossary.md").write_text(
                "# Glossary\n| Original | Translation | Type | Notes |\n"
                "| --- | --- | --- | --- |\n", "utf-8"
            )
            (book / "style-guide.md").write_text("# Style\n", "utf-8")
            (book / ".workflow/claims/chapter-000001.json").write_text(
                '{"session_id":"test-session"}', "utf-8"
            )
            chapter = {"number": 1, "unit_id": "chapter-000001",
                       "status": "translated", "source_path": "extracted/001.md",
                       "translation_path": "translated/001.md"}
            status = {"state": "running",
                      "active": {"chapter": 1, "role": "reviewer",
                                 "session_id": "test-session", "unit_id": "chapter-000001"},
                      "workflow_revision": "a" * 40, "model_calls_started": 0}
            results = []
            def persist(book_dir, unit_id, session_id, target, items):
                results.append((target, items))
                return len(items)

            report = {"evidence_version": 2, "context": "independent_session",
                      "checks": dict.fromkeys(
                          ("coverage", "meaning", "terminology", "style", "structure"), True),
                      "findings": []}
            with patch.object(runner, "ROOT", repo), \
                 patch.object(runner, "chapter_data",
                              return_value=({}, {"records": []}, [chapter])), \
                 patch.object(runner, "preliminary_context",
                              return_value=("pinned system", "review source", source)), \
                 patch.object(runner, "role_contracts",
                              return_value=("pinned reviewer", ["AGENTS.md"])), \
                 patch.object(runner, "checked_reviewer_response",
                              return_value=("PASS", report, [style_entry()])), \
                 patch.object(runner, "save_claim_bound_decisions",
                              side_effect=persist), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "cli"), \
                 patch.object(runner, "complete_for_job",
                              return_value=reviewer_payload([name()])):
                runner.process_claim("b" * 32, "sample", status, repo)
            self.assertEqual(results, [("glossary", [name()])])
            self.assertEqual(status["memory_glossary_saved"], 1)
            self.assertEqual(status["pending_style"], [style_entry()])
            self.assertIsNone(status["active"])

    def test_changed_glossary_routes_only_current_terminology_findings_to_reviewer(self):
        import hashlib
        def digest(value):
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "synthetic"
            for sub in ("extracted", "translated"):
                (book / sub).mkdir(parents=True, exist_ok=True)
            source = "The Linnet said farewell."
            target = "Зеленушка сказал прощай."
            (book / "extracted" / "001.md").write_text(source, "utf-8")
            (book / "translated" / "001.md").write_text(target, "utf-8")
            glossary = book / "glossary.md"
            old = "| Linnet | Коноплянка | персонаж | женский род |\n"
            new = "| Linnet | Зеленушка | персонаж | мужской род |\n"
            glossary.write_text(old, "utf-8")
            old_sha = digest(old)
            finding = {
                "id": "term-1", "category": "terminology", "resolved": False,
                "source_anchor": {"quote": "Linnet", "occurrence": 0},
                "translation_anchor": {"quote": "Зеленушка", "occurrence": 0},
            }
            record = {
                "record_id": "f" * 32, "unit_id": "chapter-000001",
                "outcome": "CORRECTIONS_REQUIRED",
                "source_sha256": digest(source),
                "translation_sha256": digest(target),
                "shared_state_revisions": {"glossary": old_sha},
                "quality_report": {"evidence_version": 2, "findings": [finding]},
            }
            chapter = {
                "number": 1, "status": "translated", "unit_id": "chapter-000001",
                "source_path": "extracted/001.md",
                "translation_path": "translated/001.md",
                "translation_acceptance": {
                    "source_sha256": digest(source),
                    "translation_sha256": digest(target),
                },
            }
            ledger = {"records": [record]}
            decision = json.dumps({
                "operation": "correct_translation", "unit_id": chapter["unit_id"],
                "chapter_number": 1,
            })
            with patch.object(runner, "ROOT", root), \\
                 patch.object(runner, "pinned_scripts_dir",
                              return_value=ROOT / "scripts"), \\
                 patch.object(runner, "chapter_data",
                              return_value=({}, ledger, [chapter])), \\
                 patch.object(runner, "cli", return_value=decision):
                self.assertEqual(runner.canonical_dispatch("synthetic", {})[0], "corrector")
                glossary.write_text(new, "utf-8")
                status = {}
                self.assertEqual(runner.canonical_dispatch("synthetic", status)[0], "reviewer")
                self.assertEqual(status["glossary_recheck_unit"], chapter["unit_id"])
                # The same exception cannot waive meaning errors or a stale artifact.
                record["quality_report"]["findings"][0]["category"] = "meaning"
                self.assertEqual(runner.canonical_dispatch("synthetic", {})[0], "corrector")
                record["quality_report"]["findings"][0]["category"] = "terminology"
                chapter["translation_acceptance"]["translation_sha256"] = digest("modified")
                self.assertFalse(runner.glossary_only_recheck_needed(
                    "synthetic", chapter, record))

    def test_missing_or_ungrounded_reviewer_proposal_does_not_corrupt_memory(self):
        row = name("Mara", "Мара")
        accepted, dropped = runner.filter_ungrounded_glossary_proposals(
            [row], "Mara appears.", "Никого нет.")
        self.assertEqual(accepted, [])
        self.assertEqual(dropped, 1)


if __name__ == "__main__":
    unittest.main()
