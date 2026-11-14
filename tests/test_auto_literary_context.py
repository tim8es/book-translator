"""Contract tests for manifest-routed automatic literary prompts and memory."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner
from literary_context import (MAX_SHARED_CHARS, full_shared_context,
                              parse_literary_review, parse_translation, role_contracts,
                              save_claim_bound_decisions, updated_glossary, updated_style,
                              model_glossary_context)
from omniroute import ModelError


class LiteraryContextTests(unittest.TestCase):
    def engine(self, root: Path) -> Path:
        engine = root / "pinned"
        engine.mkdir()
        (engine / "docs").mkdir()
        contracts = {"global": "AGENTS.md", "translation": "docs/TRANSLATION.md",
                     "translator": "docs/TRANSLATOR.md", "reviewer": "docs/REVIEWER.md"}
        for role, path in contracts.items():
            dest = engine / path
            dest.write_text("pinned " + role, encoding="utf-8")
        (engine / "agent-manifest.json").write_text(json.dumps({
            "schema_version": 3, "contracts": contracts,
            "context_profiles": {
                "translator": ["global", "translation", "translator"],
                "reviewer": ["global", "translation", "reviewer"]},
            "context_overlays": {}}), encoding="utf-8")
        return engine

    def test_pinned_manifest_selects_only_role_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            translator, files = role_contracts(engine, "translator")
            self.assertEqual(files, ["AGENTS.md", "docs/TRANSLATION.md", "docs/TRANSLATOR.md"])
            self.assertIn("pinned translator", translator)
            self.assertNotIn("pinned reviewer", translator)
            reviewer, files = role_contracts(engine, "reviewer")
            self.assertIn("pinned reviewer", reviewer)
            self.assertNotIn("pinned translator", reviewer)

    def test_manifest_profile_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.engine(Path(tmp))
            manifest_path = engine / "agent-manifest.json"
            data = json.loads(manifest_path.read_text("utf-8"))
            data["context_profiles"]["translator"] = ["global"]
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required"):
                role_contracts(engine, "translator")

    def test_translator_returns_complete_artifact_and_decisions(self):
        obj = {"translation": "# Глава\n\nМара у двери.\n",
               "glossary": [{"original": "Mara", "translation": "Мара",
                             "type": "person", "notes": "Protagonist"}],
               "style_guide": [{"source_pattern": "restrained third-person",
                                "translation_approach": "keep simple narration",
                                "evidence": "first paragraph"}]}
        translation, glossary, style = parse_translation("```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```")
        self.assertIn("Мара", translation)
        self.assertEqual(glossary[0]["original"], "Mara")
        self.assertEqual(style[0]["source_pattern"], "restrained third-person")
        for bad in ("only text", json.dumps({"translation": "Hi"})):
            with self.assertRaises(ModelError):
                parse_translation(bad)

    def test_review_requires_literary_decisions_field_and_actual_checks(self):
        review = {"outcome": "PASS", "checks": {
            "coverage": True, "meaning": True, "terminology": True,
            "style": True, "structure": True}, "findings": [],
            "style_guide": []}
        outcome, report, style = parse_literary_review(json.dumps(review))
        self.assertEqual(outcome, "PASS")
        self.assertEqual(style, [])
        self.assertTrue(report["checks"]["terminology"])
        del review["style_guide"]
        with self.assertRaises(ModelError):
            parse_literary_review(json.dumps(review))

    def test_v2_review_requires_complete_anchored_findings(self):
        review = {"evidence_version": 2, "outcome": "CORRECTIONS_REQUIRED", "checks": {
            "coverage": True, "meaning": True, "terminology": True,
            "style": True, "structure": True}, "findings": [{
                "id": "meaning-1", "category": "meaning", "severity": "major",
                "detail": "Possibility became certainty.", "resolved": False,
                "source_anchor": {"quote": "She might have known.", "occurrence": 0},
                "translation_anchor": {"quote": "Она знала.", "occurrence": 0},
                "resolves": None}], "style_guide": []}
        outcome, report, _style = parse_literary_review(json.dumps(review))
        self.assertEqual(outcome, "CORRECTIONS_REQUIRED")
        self.assertEqual(report["evidence_version"], 2)
        self.assertEqual(report["findings"][0]["id"], "meaning-1")
        review["findings"] = [{"severity": "major", "detail": "Incomplete finding.", "resolved": False}]
        with self.assertRaisesRegex(ModelError, "reviewer evidence is invalid"):
            parse_literary_review(json.dumps(review))

    def test_structured_glossary_context_is_language_independent(self):
        markdown = (
            "# Glossary\n| Original | Translation | Type | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Cade | Кейд | character | Inflected in running prose |\n"
            "| Dillon | Dillon | character | Unchanged in this edition |\n"
        )
        model_input = json.loads(model_glossary_context(
            markdown, source_language="en", target_language="ru"))
        self.assertEqual(model_input["source_language"], "en")
        self.assertEqual(model_input["target_language"], "ru")
        self.assertEqual(model_input["entries"][0]["translation"], "Кейд")
        self.assertEqual(model_input["entries"][1]["translation"], "Dillon")
        self.assertIn("grammar", model_input["interpretation"])
        other = json.loads(model_glossary_context(
            markdown, source_language="en", target_language="fr"))
        self.assertEqual(other["target_language"], "fr")

    def test_glossary_recheck_feature_uses_only_book_pinned_validator(self):
        old_snapshot = {"glossary": "a" * 64}
        def legacy(report, *, previous, source_sha256, translation_sha256, outcome):
            return None
        def upgraded(report, *, previous, source_sha256, translation_sha256,
                     outcome, current_shared_state_revisions=None):
            return None
        claim = {"shared_state_revisions": old_snapshot}
        self.assertEqual(runner.reviewer_lineage_options(legacy, claim), {})
        self.assertEqual(
            runner.reviewer_lineage_options(upgraded, claim),
            {"current_shared_state_revisions": old_snapshot},
        )
        with self.assertRaisesRegex(runner.JobError, "valid glossary revision"):
            runner.reviewer_lineage_options(
                upgraded, {"shared_state_revisions": {"glossary": "invalid"}})
        self.assertEqual(runner.reviewer_lineage_options(legacy, {}), {})

    def test_auto_routes_old_glossary_review_back_to_reviewer(self):
        chapter = {"unit_id": "chapter-000027", "number": 27,
                   "status": "translated",
                   "translation_acceptance": {"translation_sha256": "accepted"}}
        record = {"unit_id": "chapter-000027",
                  "outcome": "CORRECTIONS_REQUIRED",
                  "translation_sha256": "accepted",
                  "shared_state_revisions": {"glossary": "old-glossary"}}
        with patch.object(runner, "chapter_data", return_value=({}, {}, [chapter])), \
             patch.object(runner, "approved_unit_ids", return_value=set()), \
             patch.object(runner, "latest_record", return_value=record), \
             patch.object(runner, "fingerprint", return_value="new-glossary"):
            role, selected = runner.next_operation("book", {})
        self.assertEqual(role, "reviewer")
        self.assertEqual(selected["unit_id"], "chapter-000027")

    def test_auto_skips_ungrounded_glossary_without_rewriting_text(self):
        proposals = [{"original": "Cade", "translation": "Cade",
                      "type": "character", "notes": "unchanged"}]
        kept, rejected = runner.filter_ungrounded_glossary_proposals(
            proposals, "Cade visited.", "Кейд пришёл.")
        self.assertEqual((kept, rejected), ([], 1))
        kept, rejected = runner.filter_ungrounded_glossary_proposals(
            [{**proposals[0], "translation": "Кейд"}],
            "Cade visited.", "С Кейдом встретились.")
        self.assertEqual(rejected, 0)
        self.assertEqual(len(kept), 1)
        kept, rejected = runner.filter_ungrounded_glossary_proposals(
            proposals, "Cade visited.", "Cade arrived.")
        self.assertEqual((kept, rejected), (proposals, 0))

    def test_glossary_append_is_idempotent_and_conflicts_fail(self):
        initial = "# Glossary\n\n| Original | Translation | Type | Notes |\n| --- | --- | --- | --- |\n"
        value = {"original": "Northbridge", "translation": "Нортбридж",
                 "type": "place", "notes": "Town"}
        first = updated_glossary(initial, [value])
        self.assertIn("| Northbridge | Нортбридж |", first)
        self.assertEqual(first, updated_glossary(first, [{**value, "notes": "Town, recurring"}]))
        with self.assertRaisesRegex(ValueError, "conflicting glossary"):
            updated_glossary(first, [{**value, "translation": "Северный мост"}])

    def test_style_is_idempotent(self):
        row = {"source_pattern": "dry dialogue", "translation_approach": "restrained",
               "evidence": "chapter one"}
        first = updated_style("# Style Guide\n", [row])
        self.assertEqual(first, updated_style(first, [row]))
        self.assertIn("Automated reviewed stylistic decisions", first)

    def test_shared_context_is_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            long_value = "N" * 25000
            (root / "glossary.md").write_text(long_value, encoding="utf-8")
            (root / "style-guide.md").write_text("style", encoding="utf-8")
            self.assertEqual(full_shared_context(root)[0], long_value)
            (root / "glossary.md").write_text("N" * (MAX_SHARED_CHARS + 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refuse silent truncation"):
                full_shared_context(root)

    def test_runner_context_includes_pinned_instructions_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.engine(root)
            book = root / "books" / "test"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            (book / "extracted" / "one.md").write_text("Mara walked home.", encoding="utf-8")
            (book / "translated" / "one.md").write_text("Мара вернулась домой.", encoding="utf-8")
            (book / "metadata.json").write_text('{"target_language": "ru", "title": "Test"}', encoding="utf-8")
            (book / "glossary.md").write_text("Mara = Мара", encoding="utf-8")
            (book / "style-guide.md").write_text("restrained narration", encoding="utf-8")
            chapter = {"unit_id": "chapter-000001", "source_path": "extracted/one.md",
                       "translation_path": "translated/one.md", "status": "translated"}
            with patch.object(runner, "ROOT", root), patch.object(
                    runner, "chapter_data", return_value=({}, {}, [chapter])):
                system, user, original = runner.context(engine, "test", chapter, "reviewer",
                    pending_style=[{"source_pattern": "short clauses",
                                    "translation_approach": "concise",
                                    "evidence": "chapter one"}])
            self.assertIn("pinned reviewer", system)
            self.assertNotIn("pinned translator", system)
            self.assertIn("Mara = Мара", user)
            self.assertIn("CURRENT ACCEPTED TRANSLATION", user)
            self.assertIn("A check boolean describes", user)
            self.assertIn("not whether the translation passed", user)
            self.assertIn("checks:false", user)
            self.assertIn("short clauses", user)
            self.assertEqual(original, "Mara walked home.")

    def test_structural_span_binds_short_prior_context_without_losing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.engine(root)
            book = root / "books" / "test"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            previous = {"unit_id": "chapter-000001", "status": "reviewed",
                        "source_path": "extracted/one.md",
                        "translation_path": "translated/one.md"}
            current = {"unit_id": "chapter-000002", "status": "extracted",
                       "source_path": "extracted/two.md",
                       "translation_path": "translated/two.md"}
            source = "Mara walked home."
            (book / "extracted" / "two.md").write_text(source, encoding="utf-8")
            prior = "OLDER_CONTEXT" + "X" * 6000 + "RECENT_CONTEXT" + "Y" * 1500
            (book / "translated" / "one.md").write_text(prior, encoding="utf-8")
            (book / "metadata.json").write_text(
                '{"target_language":"ru", "source_language":"en"}', encoding="utf-8")
            (book / "glossary.md").write_text("Mara = Мара", encoding="utf-8")
            (book / "style-guide.md").write_text("restrained", encoding="utf-8")
            with patch.object(runner, "ROOT", root), patch.object(
                    runner, "chapter_data", return_value=({}, {}, [previous, current])):
                _system, bounded, original = runner.context(
                    engine, "test", current, "translator",
                    structural_segments=[{"id": "s00001", "source": source}])
                _system, full, _ = runner.context(engine, "test", current, "translator")
            self.assertEqual(original, source)
            self.assertIn(source, bounded)
            self.assertIn("RECENT_CONTEXT", bounded)
            self.assertNotIn("OLDER_CONTEXT", bounded)
            self.assertIn("OLDER_CONTEXT", full)
            self.assertLess(len(bounded), len(full) - 4000)

    def test_reviewer_recheck_prompt_has_explicit_json_resolves_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.engine(root)
            book = root / "books" / "test"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            (book / "extracted" / "one.md").write_text("Mara argues.", encoding="utf-8")
            (book / "translated" / "one.md").write_text("Мара говорит.", encoding="utf-8")
            (book / "metadata.json").write_text('{"target_language":"ru"}', encoding="utf-8")
            (book / "glossary.md").write_text("Mara = Мара", encoding="utf-8")
            (book / "style-guide.md").write_text("concise dialogue", encoding="utf-8")
            chapter = {"unit_id": "chapter-000001", "source_path": "extracted/one.md",
                       "translation_path": "translated/one.md", "status": "translated"}
            record = {"unit_id": "chapter-000001", "sequence": 4,
                      "record_id": "record-4", "outcome": "CORRECTIONS_REQUIRED",
                      "quality_report": {"evidence_version": 2,
                          "checks": {key: True for key in
                                     ("coverage", "meaning", "terminology", "style", "structure")},
                          "findings": [{"id": "register-1", "category": "register",
                              "severity": "minor", "detail": "Register mismatch.",
                              "resolved": False,
                              "source_anchor": {"quote": "argues", "occurrence": 0},
                              "translation_anchor": {"quote": "говорит", "occurrence": 0},
                              "resolves": None}]}}
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "chapter_data", return_value=({}, {"records": [record]}, [chapter])), \
                 patch.object(runner, "role_contracts", return_value=("pinned reviewer", ["AGENTS.md"])), \
                 patch.object(runner, "focused_review_context", return_value=None):
                _system, user, _source = runner.context(engine, "test", chapter, "reviewer")
            self.assertIn(json.dumps({"record_id": "record-4", "finding_id": "register-1"}), user)
            self.assertIn("prior unresolved finding", user)
            self.assertIn("PRIOR UNRESOLVED FINDINGS TO RECHECK", user)
            self.assertIn('"detail": "Register mismatch."', user)
            self.assertIn("translation_anchor must quote the supplied current accepted translation", user)
            self.assertIn("do not copy a stale anchor from the previous record", user)
            self.assertIn("Every non-null anchor quote must occur exactly once", user)
            self.assertIn("lengthen it with adjacent exact words", user)

    def test_memory_only_correction_triggers_fresh_reviewer(self):
        chapter = {"unit_id": "chapter-000001", "number": 1, "status": "translated",
                   "translation_acceptance": {
                       "source_sha256": "b" * 64, "translation_sha256": "a" * 64}}
        record = {"unit_id": "chapter-000001", "sequence": 1, "outcome": "CORRECTIONS_REQUIRED",
                  "source_sha256": "b" * 64, "translation_sha256": "a" * 64,
                  "quality_report": {"findings": [{"id": "register-1", "resolved": False}]}}
        with patch.object(runner, "chapter_data",
                          return_value=({}, {"records": [record]}, [chapter])), patch.object(
                          runner, "approved_unit_ids", return_value=set()):
            self.assertEqual(runner.next_operation("test", {"correction_rounds": {}})[0], "corrector")
            self.assertEqual(runner.next_operation("test", {
                "correction_rounds": {}, "memory_recheck_unit": "chapter-000001"})[0], "reviewer")

    def test_canonical_proposal_manager_persists_real_glossary_decision(self):
        from workflow.filesystem import FilesystemStorage
        from workflow.repository import WorkflowStateRepository
        from workflow.schemas import SchemaKind
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "books" / "test"
            book.mkdir(parents=True)
            (book / "glossary.md").write_text(
                "# Glossary\n\n| Original | Translation | Type | Notes |\n"
                "| --- | --- | --- | --- |\n", encoding="utf-8")
            (book / "style-guide.md").write_text("# Style guide\n", encoding="utf-8")
            storage = FilesystemStorage(book)
            repository = WorkflowStateRepository(storage)
            now = datetime.now(timezone.utc)
            claim = {
                "schema_version": 1, "claim_id": "a" * 32,
                "unit_id": "chapter-000001", "role": "translator",
                "session_id": "canonical-test-worker", "base_revision": "a" * 64,
                "base_commit": "b" * 40, "workflow_revision": "c" * 40,
                "shared_state_revisions": {
                    "glossary": hashlib.sha256((book / "glossary.md").read_bytes()).hexdigest(),
                    "style_guide": hashlib.sha256((book / "style-guide.md").read_bytes()).hexdigest()},
                "claimed_at": (now - timedelta(minutes=5)).isoformat(),
                "expires_at": (now + timedelta(minutes=30)).isoformat(),
            }
            repository.create(".workflow/claims/chapter-000001.json",
                              SchemaKind.CLAIM, claim)
            row = {"original": "Northbridge", "translation": "Нортбридж",
                   "type": "place", "notes": "Town name"}
            saved = save_claim_bound_decisions(book, "chapter-000001",
                                              "canonical-test-worker", "glossary", [row])
            self.assertEqual(saved, 1)
            self.assertIn("| Northbridge | Нортбридж |",
                          (book / "glossary.md").read_text("utf-8"))
            self.assertEqual(save_claim_bound_decisions(
                book, "chapter-000001", "canonical-test-worker", "glossary", [row]), 0)
            proposals = list((book / ".workflow" / "proposals").glob("*.json"))
            self.assertEqual(len(proposals), 2)
            self.assertEqual(sum(path.name.endswith(".resolution.json") for path in proposals), 1)

    def test_dispatch_follows_pinned_canonical_resume(self):
        chapter = {"unit_id": "chapter-000001", "number": 1, "status": "extracted"}
        ledger = {"records": []}
        with patch.object(runner, "chapter_data", return_value=({}, ledger, [chapter])), \
             patch.object(runner, "approved_unit_ids", return_value=set()), \
             patch.object(runner, "cli") as cli:
            for operation, expected in (
                ("translate", "translator"), ("review", "reviewer"),
                # Missing findings in an Auto/ChatOps handoff require re-review.
                ("correct_translation", "reviewer"), ("accept_review", "promote")
            ):
                cli.return_value = json.dumps({
                    "operation": operation, "unit_id": "chapter-000001",
                    "chapter_number": 1})
                role, selected = runner.canonical_dispatch("test", {"correction_rounds": {}})
                self.assertEqual(role, expected)
                self.assertEqual(selected, chapter)
            cli.return_value = json.dumps({"operation": "complete"})
            self.assertEqual(runner.canonical_dispatch("test", {})[0], "finalize")
            cli.return_value = json.dumps({"operation": "blocked", "reason": "unit_claimed"})
            with self.assertRaisesRegex(runner.JobError, "unit_claimed"):
                runner.canonical_dispatch("test", {})

    def test_fourth_corrections_required_review_defers_and_dispatches_next_unit(self):
        first = {"unit_id": "chapter-000001", "number": 1, "status": "translated",
                 "translation_acceptance": {"source_sha256": "b" * 64,
                                             "translation_sha256": "a" * 64}}
        second = {"unit_id": "chapter-000002", "number": 2, "status": "extracted"}
        ledger = {"records": [{"unit_id": first["unit_id"], "sequence": sequence,
                               "outcome": "CORRECTIONS_REQUIRED",
                               "source_sha256": "b" * 64,
                               "translation_sha256": str(sequence)}
                              for sequence in range(1, 5)]}
        status = {"correction_rounds": {first["unit_id"]: 3}}
        workflow = ModuleType("workflow")
        workflow.__path__ = []
        operations = ModuleType("workflow.operations")
        operations.next_unit_operation = lambda lifecycle, review: (
            "translate" if lifecycle == "extracted" else
            {"pass": "accept_review", "corrections_required": "correct_translation",
             "missing": "review", "stale": "review"}.get(review)
        )
        with patch.dict(sys.modules, {"workflow": workflow,
                                      "workflow.operations": operations}), \
             # This fixture exercises the adapter for an older pinned workflow
             # that does not include the newer canonical attention queue.
             patch.object(runner, "ROOT", Path("/__legacy_workflow_without_attention__")), \
             patch.object(runner, "chapter_data", return_value=({}, ledger, [first, second])), \
             patch.object(runner, "approved_unit_ids", return_value=set()), \
             patch.object(runner, "cli", return_value=json.dumps({
                 "operation": "correct_translation", "unit_id": first["unit_id"],
                 "chapter_number": 1})):
            role, selected = runner.canonical_dispatch("test", status)
            self.assertEqual((role, selected), ("translator", second))
            self.assertEqual(status["deferred_review_units"], [first["unit_id"]])
            second["status"] = "reviewed"
            self.assertEqual(runner.next_operation("test", status), ("review-deferred", None))
            ledger["records"].append({"unit_id": first["unit_id"], "sequence": 5,
                                      "outcome": "PASS", "source_sha256": "b" * 64,
                                      "translation_sha256": "a" * 64})
            self.assertTrue(runner.deferred_review_has_pass(ledger, first))
            self.assertEqual(runner.next_operation("test", status), ("promote", first))
        self.assertEqual(first["status"], "translated")

    def test_correction_limit_resume_defers_instead_of_authorizing_fifth_review(self):
        chapter = {"unit_id": "chapter-000014",
                   "translation_acceptance": {"source_sha256": "b" * 64}}
        ledger = {"records": [{"unit_id": chapter["unit_id"], "sequence": sequence,
                               "source_sha256": "b" * 64,
                               "outcome": "CORRECTIONS_REQUIRED"}
                              for sequence in range(1, 5)]}
        status = {"correction_rounds": {chapter["unit_id"]: 3},
                  "correction_extensions": {}}
        runner.prepare_correction_limit_resume(status, ledger, chapter)
        self.assertEqual(status["deferred_review_units"], [chapter["unit_id"]])
        self.assertNotIn("correction_extension_authorized_unit", status)

    def test_model_call_cap_fails_closed_without_provider_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "test"
            claim_folder = book / ".workflow" / "claims"
            claim_folder.mkdir(parents=True)
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            (claim_folder / "chapter-000001.json").write_text(json.dumps({
                "session_id": "owner"}), encoding="utf-8")
            chapter = {"unit_id": "chapter-000001", "number": 1,
                       "status": "translated"}
            status = {"active": {"chapter": 1, "role": "reviewer",
                                 "session_id": "owner"},
                      "workflow_revision": "f" * 40,
                      "model_calls_started": 1}
            with patch.dict("os.environ", {"OMNIROUTE_MODEL_CALL_LIMIT": "1"}), \
                 patch.object(runner, "ROOT", root), \
                 patch.object(runner, "chapter_data", return_value=({}, {}, [chapter])), \
                 patch.object(runner, "context", return_value=("system", "user", "source")), \
                 patch.object(runner, "role_contracts", return_value=("system", ["AGENTS.md"])), \
                 patch.object(runner, "checkpoint") as checkpoint, \
                 patch.object(runner, "cli") as cli, \
                 patch.object(runner, "complete") as complete:
                runner.process_claim("c" * 32, "test", status, root)
            complete.assert_not_called()
            self.assertEqual(status["state"], "blocked")
            self.assertEqual(status["phase"], "model-call-limit-reached")
            self.assertEqual(status["model_calls_started"], 1)
            self.assertIsNone(status["active"])
            self.assertEqual(cli.call_args.args[0], "release")
            checkpoint.assert_called_once()

    def test_model_call_cap_rejects_invalid_settings(self):
        with patch.dict("os.environ", {"OMNIROUTE_MODEL_CALL_LIMIT": "0"}):
            with self.assertRaisesRegex(runner.JobError, "integer from"):
                runner.model_call_limit()
        with patch.dict("os.environ", {"OMNIROUTE_MODEL_CALL_LIMIT": "3"}):
            self.assertEqual(runner.model_call_limit(), 3)
        with patch.dict("os.environ", {"OMNIROUTE_MODEL_CALL_LIMIT": "400"}):
            self.assertEqual(runner.model_call_limit(), 400)
        with patch.dict("os.environ", {"OMNIROUTE_MODEL_CALL_LIMIT": "401"}):
            with self.assertRaisesRegex(runner.JobError, "integer from"):
                runner.model_call_limit()

    def test_failed_model_call_saves_current_context_and_unit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "test"
            claims = book / ".workflow" / "claims"
            claims.mkdir(parents=True)
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            (claims / "chapter-000012.json").write_text(json.dumps({
                "session_id": "owner"}), encoding="utf-8")
            chapter = {"unit_id": "chapter-000012", "number": 12,
                       "status": "translated"}
            status = {"active": {"chapter": 12, "role": "reviewer",
                                 "session_id": "owner"},
                      "workflow_revision": "f" * 40,
                      "model_calls_started": 70, "failures": 2,
                      "blocked_unit_id": "chapter-000011"}
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "chapter_data", return_value=({}, {}, [chapter])), \
                 patch.object(runner, "latest_record", return_value=None), \
                 patch.object(runner, "context", return_value=("system", "review prompt", "source")), \
                 patch.object(runner, "role_contracts", return_value=("", ["AGENTS.md"])), \
                 patch.object(runner, "model_call_limit", return_value=120), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "cli"), \
                 patch.object(runner, "complete", side_effect=ModelError("transport timeout")):
                runner.process_claim("c" * 32, "test", status, root)
            self.assertEqual(status["phase"], "model-blocked")
            self.assertEqual(status["prompt_context"]["role"], "reviewer")
            self.assertEqual(status["prompt_context"]["user_chars"], len("review prompt"))
            self.assertEqual(status["blocked_unit_id"], "chapter-000012")

    def test_invalid_reviewer_anchor_is_retried_before_canonical_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "test"
            claims = book / ".workflow" / "claims"
            claims.mkdir(parents=True)
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            (book / "translated").mkdir()
            (book / "translated" / "one.md").write_text("current translation", encoding="utf-8")
            (claims / "chapter-000012.json").write_text(json.dumps({
                "session_id": "owner"}), encoding="utf-8")
            chapter = {"unit_id": "chapter-000012", "number": 12,
                       "source_path": "extracted/one.md",
                       "translation_path": "translated/one.md", "status": "translated"}
            status = {"active": {"chapter": 12, "role": "reviewer",
                                 "session_id": "owner"},
                      "workflow_revision": "f" * 40,
                      "model_calls_started": 75, "failures": 0}
            workflow = ModuleType("workflow")
            workflow.__path__ = []
            anchors = ModuleType("workflow.review_anchors")
            anchors.verify_evidence_anchors = unittest.mock.Mock(
                side_effect=ValueError("findings[0].translation_anchor is absent"))
            anchors.verify_correction_lineage = unittest.mock.Mock()
            report = {"evidence_version": 2, "context": "independent_session",
                      "checks": {key: True for key in
                                 ("coverage", "meaning", "terminology", "style", "structure")},
                      "findings": []}
            with patch.dict(sys.modules, {"workflow": workflow,
                                          "workflow.review_anchors": anchors}), \
                 patch.object(runner, "ROOT", root), \
                 patch.object(runner, "chapter_data", return_value=({}, {}, [chapter])), \
                 patch.object(runner, "latest_record", return_value=None), \
                 patch.object(runner, "context", return_value=("system", "review prompt", "source text")), \
                 patch.object(runner, "role_contracts", return_value=("", ["AGENTS.md"])), \
                 patch.object(runner, "model_call_limit", return_value=120), \
                 patch.object(runner, "parse_literary_review", return_value=("PASS", report, [])), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "cli") as cli, \
                 patch.object(runner, "complete", return_value="response"):
                runner.process_claim("c" * 32, "test", status, root)
            self.assertEqual(status["phase"], "model-retry")
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["failures"], 1)
            # One initial reviewer attempt plus one safely reserved repair.
            self.assertEqual(anchors.verify_evidence_anchors.call_count, 2)
            self.assertEqual(status["model_calls_started"], 77)
            self.assertFalse(any(call.args[0] == "review-record" for call in cli.call_args_list))

    def test_third_invalid_reviewer_report_defers_only_unit_without_review_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "synthetic"
            (book / ".workflow" / "claims").mkdir(parents=True)
            (book / "extracted").mkdir()
            (book / "translated").mkdir()
            source, target = b"one source paragraph", b"one translated paragraph"
            (book / "extracted" / "one.md").write_bytes(source)
            (book / "translated" / "one.md").write_bytes(target)
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            (book / ".workflow" / "claims" / "chapter-000001.json").write_text(
                json.dumps({"session_id": "owner"}), encoding="utf-8")
            chapter = {"unit_id": "chapter-000001", "number": 1,
                       "source_path": "extracted/one.md",
                       "translation_path": "translated/one.md", "status": "translated",
                       "translation_acceptance": {
                           "source_sha256": hashlib.sha256(source).hexdigest(),
                           "translation_sha256": hashlib.sha256(target).hexdigest()}}
            status = {"state": "running", "phase": "claimed",
                      "active": {"role": "reviewer", "chapter": 1,
                                 "unit_id": "chapter-000001", "session_id": "owner"},
                      "workflow_revision": "f" * 40,
                      "model_calls_started": 70, "failures": 2}
            bad_review = ModelError(
                "reviewer evidence is invalid: every literary check must have been performed")
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "chapter_data", return_value=({}, {"records": []}, [chapter])), \
                 patch.object(runner, "context", return_value=("system", "prompt", source.decode())), \
                 patch.object(runner, "role_contracts", return_value=("system", ["AGENTS.md"])), \
                 patch.object(runner, "parse_literary_review", side_effect=bad_review), \
                 patch.object(runner, "model_call_limit", return_value=150), \
                 patch.object(runner, "complete", return_value="{}"), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "cli") as cli:
                runner.process_claim("c" * 32, "synthetic", status, root)
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["phase"], "model-unit-deferred")
            self.assertEqual(status["failures"], 0)
            self.assertEqual(status["last_error_code"], "review_checks_incomplete")
            self.assertEqual(status["role_protocol_failures"]["chapter-000001:reviewer"], 1)
            self.assertEqual(status["deferred_review_units"], ["chapter-000001"])
            self.assertTrue(any(call.args[0] == "release" for call in cli.call_args_list))
            self.assertFalse(any(call.args[0] in {"accept-review", "review-record"}
                                 for call in cli.call_args_list))

    def test_false_reviewer_checks_trigger_focused_recheck_not_boolean_rewrite(self):
        response = json.dumps({
            "evidence_version": 2, "outcome": "CORRECTIONS_REQUIRED",
            "checks": {"coverage": False, "meaning": True,
                       "terminology": True, "style": True, "structure": True},
            "findings": [], "style_guide": []})
        status = {"model_calls_started": 5,
                  "active": {"role": "reviewer", "chapter": 1,
                             "unit_id": "chapter-000001", "session_id": "claim"}}
        error = ModelError(
            "reviewer evidence is invalid: every literary check must have been performed")
        with patch.object(runner, "model_call_limit", return_value=30), \
             patch.object(runner, "checkpoint"), \
             patch.object(runner, "complete_for_job", return_value="{}") as call:
            result = runner.retry_reviewer_anchor_evidence(
                "c" * 32, "test", status, {}, "pinned reviewer", "review source",
                response, error, "source", b"translation")
        self.assertEqual(result, "{}")
        self.assertEqual(status["model_calls_started"], 6)
        self.assertEqual(status["phase"], "review-schema-repair-reserved")
        prompt = call.call_args.args[-1]
        self.assertIn("INCOMPLETE LITERARY CHECKS", prompt)
        self.assertIn('"coverage"', prompt)
        self.assertIn("never return PASS merely to satisfy", prompt)
        self.assertIn("must remain false and cannot be accepted", prompt)
        self.assertNotIn("mark every check true", prompt)

    def test_claim_includes_frozen_literary_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "books" / "test"
            book.mkdir(parents=True)
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            status = {"last_error": "stale reviewer error"}
            with patch.object(runner, "ROOT", root), patch.object(
                runner, "git", return_value="a" * 40), patch.object(
                runner, "cli") as cli, patch.object(runner, "checkpoint"):
                runner.claim("f" * 32, "test", status, "translator",
                             {"number": 1, "unit_id": "chapter-000001"})
            self.assertNotIn("last_error", status)
            args = cli.call_args.args
            self.assertIn("--base-commit", args)
            self.assertEqual(args[args.index("--glossary-revision") + 1],
                             hashlib.sha256(b"glossary").hexdigest())
            self.assertEqual(args[args.index("--style-guide-revision") + 1],
                             hashlib.sha256(b"style").hexdigest())


if __name__ == "__main__":
    unittest.main()
