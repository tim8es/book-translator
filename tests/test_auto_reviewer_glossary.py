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

    def test_missing_or_ungrounded_reviewer_proposal_does_not_corrupt_memory(self):
        row = name("Mara", "Мара")
        accepted, dropped = runner.filter_ungrounded_glossary_proposals(
            [row], "Mara appears.", "Никого нет.")
        self.assertEqual(accepted, [])
        self.assertEqual(dropped, 1)


if __name__ == "__main__":
    unittest.main()
