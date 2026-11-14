"""Durable provider outages, safe unit isolation and optional failover."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import nullcontext
from io import BytesIO
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker
from omniroute import (
    ModelError, TransientModelError, complete, config,
)


class ProviderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.chapter = {"number": 1, "unit_id": "chapter-000001",
                        "status": "extracted"}
        self.status = {"state": "running", "failures": 1,
                       "active": {"role": "translator", "chapter": 1,
                                  "session_id": "test"}}
        self.env = {
            "OMNIROUTE_BASE_URL": "https://model.example/v1",
            "OMNIROUTE_API_KEY": "test-token",
            "OMNIROUTE_TRANSLATOR_MODEL": "test-primary",
            "OMNIROUTE_TRANSLATOR_FALLBACK_MODEL": "test-backup",
        }

    def test_json_object_transport_accepts_json_key_mode_and_records_only_counts(self):
        payload = {
            "choices": [{"finish_reason": "stop",
                         "message": {"content": '{"probe":"ok"}'}}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 12,
                      "total_tokens": 72},
            "model": "private-model-identifier",
        }
        body = json.dumps(payload).encode("utf-8")
        with patch.dict(os.environ, self.env), \
             patch("omniroute.urlopen", return_value=nullcontext(BytesIO(body))) as post:
            answer = complete("TRANSLATOR", "system", "probe")
        self.assertEqual(json.loads(answer), {"probe": "ok"})
        outgoing = json.loads(post.call_args.args[0].data)
        self.assertEqual(outgoing["response_format"], {"type": "json_object"})
        self.assertEqual(answer.diagnostics["finish_reason"], "stop")
        self.assertEqual(answer.diagnostics["response_format"], "json_object")
        self.assertEqual(answer.diagnostics["prompt_tokens"], 60)
        self.assertEqual(answer.diagnostics["completion_tokens"], 12)
        self.assertEqual(answer.diagnostics["total_tokens"], 72)
        self.assertEqual(answer.diagnostics["response_chars"], len('{"probe":"ok"}'))
        self.assertNotIn("model", answer.diagnostics)
        self.assertNotIn("probe", json.dumps(answer.diagnostics))
        self.assertNotIn("test-token", repr(answer.diagnostics))

    def test_provider_length_rejection_is_explicit_but_no_text_leaks(self):
        payload = {"choices": [{"finish_reason": "length",
                                "message": {"content": "private text"}}]}
        with patch.dict(os.environ, self.env), \
             patch("omniroute.urlopen", return_value=nullcontext(
                 BytesIO(json.dumps(payload).encode("utf-8")))):
            with self.assertRaisesRegex(ModelError, "length") as error:
                complete("TRANSLATOR", "system", "probe")
        self.assertNotIn("private text", str(error.exception))

    def test_safe_response_diagnostics_persist_with_current_unit_and_route(self):
        self.status["active"]["unit_id"] = self.chapter["unit_id"]
        class DiagnosticText(str):
            diagnostics = {"finish_reason": "stop", "response_format": "json_object",
                           "response_chars": 14, "completion_tokens": 12}
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", return_value=DiagnosticText('{"probe":"ok"}')):
            worker.complete_for_job(self.status, "TRANSLATOR", "system", "probe")
        recorded = self.status["model_response_diagnostics"]
        self.assertEqual(recorded["role"], "translator")
        self.assertEqual(recorded["unit_id"], self.chapter["unit_id"])
        self.assertEqual(recorded["route"], "primary")
        self.assertEqual(recorded["completion_tokens"], 12)
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", side_effect=ModelError("bad reply")):
            with self.assertRaises(ModelError):
                worker.complete_for_job(self.status, "TRANSLATOR", "system", "probe")
        self.assertNotIn("model_response_diagnostics", self.status)

    def test_http_429_is_one_classified_request_with_retry_after(self):
        error = HTTPError("https://model.example/v1/chat/completions",
                          429, "Too Many Requests",
                          {"Retry-After": "150"}, None)
        with patch.dict(os.environ, self.env), \
             patch("omniroute.urlopen", side_effect=error) as post:
            with self.assertRaises(TransientModelError) as ctx:
                complete("TRANSLATOR", "system", "source")
        post.assert_called_once()
        self.assertEqual(ctx.exception.code, "http_429")
        self.assertEqual(ctx.exception.retry_after_seconds, 150)
        self.assertNotIn("test-token", str(ctx.exception))

    def test_nontransient_http_error_cannot_be_scheduled(self):
        error = HTTPError("https://model.example/v1/chat/completions",
                          404, "Not Found", {}, None)
        with patch.dict(os.environ, self.env), \
             patch("omniroute.urlopen", side_effect=error) as post:
            with self.assertRaisesRegex(ModelError, "404") as ctx:
                complete("TRANSLATOR", "system", "source")
        self.assertNotIsInstance(ctx.exception, TransientModelError)
        post.assert_called_once()

    def test_network_failure_is_transient_but_bad_payload_is_not(self):
        with patch.dict(os.environ, self.env), \
             patch("omniroute.urlopen", side_effect=URLError("timeout")) as post:
            with self.assertRaises(TransientModelError) as ctx:
                complete("TRANSLATOR", "system", "source")
        post.assert_called_once()
        self.assertEqual(ctx.exception.code, "transport")
        self.assertIsNone(worker.translator_model_output_code(ctx.exception))

    def test_retry_deadline_is_persisted_and_exponentially_bounded(self):
        first = TransientModelError("model HTTP status 429", code="http_429",
                                    retry_after_seconds=300)
        worker.schedule_provider_retry(self.status, self.chapter, "translator", first)
        one = self.status["provider_retry"]
        self.assertEqual(one["attempts"], 1)
        self.assertEqual(one["code"], "http_429")
        self.assertFalse(worker.provider_retry_due(self.status))
        self.assertGreaterEqual(
            (datetime.fromisoformat(one["not_before"]) -
             datetime.now(timezone.utc)).total_seconds(), 290)
        second = TransientModelError("model HTTP status 503", code="http_503")
        worker.schedule_provider_retry(self.status, self.chapter, "translator", second)
        self.assertEqual(self.status["provider_retry"]["attempts"], 2)
        self.assertEqual(self.status["phase"], "provider-backoff")
        self.assertEqual(self.status["state"], "running")
        self.assertEqual(self.status["failures"], 1)
        self.assertTrue(worker.provider_retry_due({
            "provider_retry": {**self.status["provider_retry"],
                               "not_before": (datetime.now(timezone.utc)
                               - timedelta(minutes=1)).isoformat()}
        }))
        for _ in range(15):
            worker.schedule_provider_retry(
                self.status, self.chapter, "translator", second)
        deadline = datetime.fromisoformat(self.status["provider_retry"]["not_before"])
        self.assertLessEqual(
            (deadline - datetime.now(timezone.utc)).total_seconds(),
            worker.MAX_BACKOFF_SECONDS + 2)

    def test_corrupted_checkpoint_blocks_no_external_requests(self):
        self.status["provider_retry"] = {"not_before": "invalid"}
        with self.assertRaisesRegex(worker.JobError, "invalid provider retry checkpoint"):
            worker.provider_retry_due(self.status)

    def test_fallback_uses_only_configured_variable_and_current_unit(self):
        worker.schedule_provider_retry(
            self.status, self.chapter, "translator",
            TransientModelError("timeout", code="transport"))
        worker.schedule_provider_retry(
            self.status, self.chapter, "translator",
            TransientModelError("timeout", code="transport"))
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", return_value="{}") as post:
            self.assertEqual(worker.complete_for_job(
                self.status, "TRANSLATOR", "system", "source"), "{}")
        post.assert_called_once_with("TRANSLATOR_FALLBACK", "system", "source")
        self.assertEqual(self.status["provider_model_route"], "fallback")
        with patch.dict(os.environ, {**self.env,
                                     "OMNIROUTE_TRANSLATOR_FALLBACK_MODEL": ""}), \
             patch.object(worker, "complete", return_value="{}") as post:
            worker.complete_for_job(self.status, "TRANSLATOR", "system", "source")
        post.assert_called_once_with("TRANSLATOR", "system", "source")
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", return_value="{}") as post:
            self.status["active"]["chapter"] = 2
            worker.complete_for_job(self.status, "TRANSLATOR", "system", "source")
        post.assert_called_once_with("TRANSLATOR", "system", "source")
        with patch.dict(os.environ, self.env):
            self.assertEqual(config("TRANSLATOR_FALLBACK")[2], "test-backup")


    def test_repeated_same_unit_protocol_failures_select_backup(self):
        self.status["active"]["unit_id"] = self.chapter["unit_id"]
        self.status["active"]["role"] = "translator"
        self.status["role_protocol_failures"] = {
            self.chapter["unit_id"] + ":translator": 2}
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", return_value="{}") as post:
            worker.complete_for_job(self.status, "TRANSLATOR", "system", "text")
        post.assert_called_once_with("TRANSLATOR_FALLBACK", "system", "text")
        # Never use another unit's failed attempts to select its model.
        self.status["active"]["unit_id"] = "chapter-000002"
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", return_value="{}") as post:
            worker.complete_for_job(self.status, "TRANSLATOR", "system", "text")
        post.assert_called_once_with("TRANSLATOR", "system", "text")

    def test_role_scoped_fallback_never_inherits_other_role_failures(self):
        unit = self.chapter["unit_id"]
        self.status["active"].update({"role": "reviewer", "unit_id": unit})
        self.status["unit_protocol_failures"] = {unit: 5}
        self.status["role_protocol_failures"] = {unit + ":translator": 3}
        env = {**self.env, "OMNIROUTE_REVIEWER_MODEL": "review-primary",
               "OMNIROUTE_REVIEWER_FALLBACK_MODEL": "review-backup"}
        with patch.dict(os.environ, env), \
             patch.object(worker, "complete", return_value="{}") as post:
            worker.complete_for_job(self.status, "REVIEWER", "system", "source")
        post.assert_called_once_with("REVIEWER", "system", "source")
        self.status["role_protocol_failures"][unit + ":reviewer"] = 2
        with patch.dict(os.environ, env), \
             patch.object(worker, "complete", return_value="{}") as post:
            worker.complete_for_job(self.status, "REVIEWER", "system", "source")
        post.assert_called_once_with("REVIEWER_FALLBACK", "system", "source")

    def test_structural_corrector_uses_translator_route_but_corrector_error_scope(self):
        unit = self.chapter["unit_id"]
        self.status["active"].update({"role": "corrector", "unit_id": unit})
        self.status["role_protocol_failures"] = {unit + ":corrector": 2}
        with patch.dict(os.environ, self.env), \
             patch.object(worker, "complete", return_value="{}") as post:
            worker.complete_for_job(self.status, "TRANSLATOR", "system", "source")
        post.assert_called_once_with("TRANSLATOR_FALLBACK", "system", "source")

    def test_reviewer_generated_protocol_allowlist_does_not_skip_unknowns(self):
        good = {
            "reviewer evidence is invalid: every literary check must have been performed":
                "review_checks_incomplete",
            "reviewer evidence is invalid: all checks must be boolean":
                "review_evidence_invalid",
            "reviewer evidence is invalid: findings[0].translation_anchor: quote is repeated":
                "review_evidence_invalid",
            "reviewer response has invalid literary memory schema": "review_json_invalid",
            "reviewer must return JSON": "review_json_invalid",
        }
        for message, code in good.items():
            self.assertEqual(worker.reviewer_model_output_code(ModelError(message)), code)
        for failure in (
            ModelError("reviewer evidence is invalid: source file hash changed"),
            ModelError("model HTTP status 404"),
            ModelError("model output incomplete or filtered: content_filter"),
            TransientModelError("model HTTP status 429", code="http_429"),
        ):
            self.assertIsNone(worker.reviewer_model_output_code(failure))

    def test_review_protocol_quarantine_is_hash_bound_and_never_marks_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books" / "synthetic-book"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            source = b"source paragraph"
            target = "Перевод абзаца".encode("utf-8")
            (book / "extracted" / "one.md").write_bytes(source)
            (book / "translated" / "one.md").write_bytes(target)
            s_hash = hashlib.sha256(source).hexdigest()
            t_hash = hashlib.sha256(target).hexdigest()
            chapter = {"number": 1, "unit_id": "chapter-000001",
                       "status": "translated", "source_path": "extracted/one.md",
                       "translation_path": "translated/one.md",
                       "translation_acceptance": {
                           "source_sha256": s_hash, "translation_sha256": t_hash}}
            status = {"failures": worker.MAX_FAILURES, "state": "blocked"}
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "cli") as cli:
                self.assertTrue(worker.defer_failed_reviewer_unit(
                    "synthetic-book", chapter, None, status, "review_checks_incomplete"))
                cli.assert_not_called()
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["phase"], "model-unit-deferred")
            self.assertEqual(status["deferred_review_units"], ["chapter-000001"])
            self.assertEqual(status["model_review_deferral_audit"][0]["translation_sha256"],
                             t_hash)
            self.assertNotIn("PASS", repr(status))
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "cli") as cli:
                self.assertFalse(worker.defer_failed_reviewer_unit(
                    "synthetic-book", chapter, None,
                    {"failures": 3}, None))
                cli.assert_not_called()
                bad_chapter = {**chapter, "translation_acceptance": {
                    **chapter["translation_acceptance"],
                    "translation_sha256": "0" * 64}}
                with self.assertRaisesRegex(worker.JobError, "artifact changed"):
                    worker.defer_failed_reviewer_unit(
                        "synthetic-book", bad_chapter, None,
                        {"failures": 3}, "review_checks_incomplete")
                cli.assert_not_called()
            # A new canonical accepted artifact invalidates prior model deferral.
            new_chapter = {**chapter, "translation_acceptance": {
                **chapter["translation_acceptance"],
                "translation_sha256": "1" * 64}}
            worker.reconcile_deferred_model_reviews(status, [new_chapter])
            self.assertEqual(status["deferred_review_units"], [])
            self.assertEqual(status["model_review_deferral_invalidations"][0]["unit_id"],
                             "chapter-000001")

    def test_review_protocol_quarantine_uses_canonical_only_on_current_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books" / "synthetic-book"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            source = b"original"
            target = b"translation"
            (book / "extracted" / "one.md").write_bytes(source)
            (book / "translated" / "one.md").write_bytes(target)
            s_hash = hashlib.sha256(source).hexdigest()
            t_hash = hashlib.sha256(target).hexdigest()
            chapter = {"number": 1, "unit_id": "chapter-000001",
                       "status": "translated", "source_path": "extracted/one.md",
                       "translation_path": "translated/one.md",
                       "translation_acceptance": {
                           "source_sha256": s_hash, "translation_sha256": t_hash}}
            record = {"record_id": "a" * 32, "outcome": "CORRECTIONS_REQUIRED",
                      "source_sha256": s_hash, "translation_sha256": t_hash}
            status = {"failures": 3, "state": "blocked"}
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "cli") as cli:
                self.assertTrue(worker.defer_failed_reviewer_unit(
                    "synthetic-book", chapter, record, status, "review_json_invalid"))
            cli.assert_called_once_with(
                "defer-review", "synthetic-book", "1",
                "--reason", "model-review-output-exhausted")
            self.assertNotIn("deferred_review_units", status)
            self.assertEqual(status["model_review_deferral_audit"][0]["route"], "canonical")
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "cli") as cli:
                self.assertFalse(worker.defer_failed_reviewer_unit(
                    "synthetic-book", chapter,
                    {**record, "outcome": "PASS"},
                    {"failures": 3}, "review_json_invalid"))
                cli.assert_not_called()

    def test_translator_output_allowlist_excludes_transport_and_unknown(self):
        for message, expected in (
            ("translator must return a structured JSON response", "invalid_translator_output"),
            ("pre-acceptance coverage gate detected probable missing source text",
             "translation_coverage"),
            ("model transport timeout or connection failure", None),
            ("source changed during structural translation", None),
            ("model HTTP status 404", None),
        ):
            self.assertEqual(worker.translator_model_output_code(
                ModelError(message)), expected)

    def test_corrector_structural_coverage_failures_are_recoverable(self):
        for message, expected in (
            ("focused structural translator repair still omitted source text",
             "translation_coverage"),
            ("pre-acceptance coverage gate detected probable missing source text",
             "translation_coverage"),
            ("structural translation omitted content and exhausted model budget",
             "translation_coverage"),
            ("model HTTP status 404", None),
            ("model response is not a valid chat completion", None),
        ):
            self.assertEqual(worker.corrector_structural_output_code(
                "corrector", {}, ModelError(message), None), expected)
        self.assertIsNone(worker.corrector_structural_output_code(
            "reviewer", {}, ModelError("focused structural translator repair still omitted source text"), None))
        self.assertIsNone(worker.corrector_structural_output_code(
            "corrector", None, ModelError("focused structural translator repair still omitted source text"), None))
        self.assertIsNone(worker.corrector_structural_output_code(
            "corrector", {}, TransientModelError("timeout", code="transport"), None))

    def test_corrector_coverage_can_defer_only_a_current_rejected_artifact(self):
        chapter = {**self.chapter, "status": "translated",
                   "translation_acceptance": {"source_sha256": "s" * 64,
                                              "translation_sha256": "t" * 64}}
        record = {"outcome": "CORRECTIONS_REQUIRED", "source_sha256": "s" * 64,
                  "translation_sha256": "t" * 64, "record_id": "record-1"}
        self.status["failures"] = worker.MAX_FAILURES
        with patch.object(worker, "cli", return_value="ok") as cli:
            self.assertTrue(worker.defer_protocol_failed_unit(
                "synthetic-book", chapter, record,
                self.status, "translation_coverage"))
        cli.assert_called_once_with(
            "defer-review", "synthetic-book", "1", "--reason",
            "operator-attention")
        self.assertEqual(self.status["state"], "running")
        self.assertEqual(self.status["phase"], "model-unit-deferred")
        self.assertNotIn("PASS", repr(self.status))
        with patch.object(worker, "cli") as cli:
            self.assertFalse(worker.defer_protocol_failed_unit(
                "synthetic-book", chapter, record,
                {**self.status, "failures": 3}, None))
            cli.assert_not_called()

    def test_translation_deferral_requires_canonical_supported_contract(self):
        self.status["failures"] = worker.MAX_FAILURES
        with patch.object(worker, "canonical_translation_deferral_supported",
                          return_value=True), \
             patch.object(worker, "cli", return_value="ok") as cli:
            self.assertTrue(worker.defer_untranslated_unit(
                "synthetic-book", self.chapter,
                self.status, "translation_coverage"))
        cli.assert_called_once_with(
            "defer-translation", "synthetic-book", "1", "--reason",
            "model-coverage-exhausted")
        self.assertEqual(self.status["state"], "running")
        self.assertEqual(self.status["phase"], "model-unit-deferred")
        self.assertNotIn("PASS", repr(self.status))
        with patch.object(worker, "canonical_translation_deferral_supported",
                          return_value=False), \
             patch.object(worker, "cli") as cli:
            self.assertFalse(worker.defer_untranslated_unit(
                "synthetic-book", self.chapter,
                {**self.status, "failures": 3}, "invalid_translator_output"))
            cli.assert_not_called()

    def test_provider_backoff_has_no_immediate_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                worker.publish_outputs(
                    "b" * 32, {"state": "running", "phase": "provider-backoff"})
            self.assertIn("continue_queue=true", output.read_text("utf-8"))
            self.assertNotIn("continue=true", output.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
