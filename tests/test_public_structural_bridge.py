"""Reusable public structural helper bridge: bounded checkpoint integration tests."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker
from omniroute import ModelError


class FakePublicHelper:
    def spans(self, plan):
        return plan["segments"]

    def plan(self, source):
        return {
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "segments": [{"id": f"s{i:05d}", "source": f"sample {i}"}
                         for i in range(1, 8)],
        }

    def next_spans(self, plan, completed, limit=3):
        ids = {entry["id"] for entry in self.spans(plan)}
        if any(x not in ids for x in completed):
            raise ValueError("unknown completed segment")
        return [entry for entry in self.spans(plan)
                if entry["id"] not in completed][:limit]

    def parse_batch(self, answer, expected):
        try:
            data = json.loads(answer)
        except ValueError as exc:
            raise ValueError("structural translator must return JSON") from exc
        result = {}
        for entry in data["segments"]:
            if entry["id"] not in expected or entry["id"] in result:
                raise ValueError("unknown or duplicate translation segment")
            result[entry["id"]] = entry["translation"]
        return result, data["glossary"], data["style_guide"]

    @staticmethod
    def protocol_failure_code(error):
        return {
            "structural translator must return JSON": "json_invalid",
            "unknown or duplicate translation segment": "invalid_segment_id",
        }.get(error)

    @staticmethod
    def repair_guidance(selected, error):
        return json.dumps({
            "required_segment_id": selected[0]["id"],
            "previous_response_rejected": error,
        })

    @staticmethod
    def coverage_alert(source, translated):
        return False

    def render(self, plan, completed):
        if len(completed) != len(self.spans(plan)):
            raise ValueError("missing segments")
        return (" ".join(completed[item["id"]]
                         for item in self.spans(plan))
                + "\n[ref](https://example.test)\n")


class PublicHelperBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slug = "synthetic-public-helper"
        self.book = self.root / "books" / self.slug
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "glossary.md").write_text("glossary\n", encoding="utf-8")
        (self.book / "style-guide.md").write_text("style\n", encoding="utf-8")
        (self.book / "extracted" / "001.md").write_text(
            "One two three\n[ref](https://example.test)\n", encoding="utf-8")
        self.chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "source_path": "extracted/001.md",
            "translation_path": "translated/001.md",
            "status": "extracted",
        }
        self.status = {
            "state": "running", "phase": "claimed", "model_calls_started": 0,
            "workflow_revision": "a" * 40,
            "active": {"role": "translator", "chapter": 1, "session_id": "test"},
        }
        self.helper = FakePublicHelper()
        self.plan = self.helper.plan(
            (self.book / "extracted/001.md").read_text("utf-8"))

    @staticmethod
    def complete(_role, _system, user):
        selected = json.loads(user)
        return json.dumps({
            "segments": [{"id": item["id"],
                          "translation": "Переведено " + item["id"]}
                         for item in selected],
            "glossary": [], "style_guide": [],
        }, ensure_ascii=False)

    def once(self, *, complete=None):
        def context(_engine, _slug, _chapter, _role, *, structural_segments):
            return "system", json.dumps(structural_segments), "source"
        with patch.object(worker, "ROOT", self.root), \
             patch.object(worker, "context", side_effect=context), \
             patch.object(worker, "complete",
                          side_effect=complete or self.complete) as calls, \
             patch.object(worker, "role_contracts",
                          return_value=("pinned", ["AGENTS.md", "docs/TRANSLATOR.md"])), \
             patch.object(worker, "checkpoint") as checkpoint:
            result = worker.process_structural_translation(
                "f" * 32, self.slug, self.status, self.root,
                self.chapter, self.helper, self.plan)
        return result, calls.call_count, checkpoint.call_count

    def test_checkpoint_only_two_calls_and_progress_survives(self):
        first, calls, checkpoints = self.once()
        self.assertIsNone(first)
        self.assertEqual((calls, checkpoints), (2, 4))
        self.assertEqual(self.status["structural_segment_progress"]["completed"], 6)
        stage = self.book / ".workflow/auto-structural-translation/chapter-000001.json"
        self.assertEqual(len(json.loads(stage.read_text("utf-8"))["segments"]), 6)
        self.assertFalse((self.book / "translated/001.md").exists())
        self.assertEqual(self.status["structural_helper_revision"],
                         worker.PUBLIC_STRUCTURAL_HELPER_REVISION)

        second, calls, checkpoints = self.once()
        self.assertEqual((calls, checkpoints), (1, 2))
        self.assertIsNotNone(second)
        self.assertEqual(len(second[0].split("Переведено")), 8)
        self.assertTrue(second[0].endswith("[ref](https://example.test)\n"))
        # No pre-acceptance canonical write: caller owns the pinned CLI accept.
        self.assertFalse((self.book / "translated/001.md").exists())

    def test_reject_stale_helper_and_source_before_retry(self):
        self.once()
        with patch.object(worker, "ROOT", self.root):
            stage_path, stage = worker.load_structural_stage(
                self.slug, self.chapter, self.status, self.helper, self.plan)
            self.assertEqual(stage["source_sha256"], self.plan["source_sha256"])
            self.assertEqual(stage["helper_revision"],
                             worker.PUBLIC_STRUCTURAL_HELPER_REVISION)
            changed = {**self.plan, "source_sha256": "b" * 64}
            with self.assertRaisesRegex(worker.JobError, "stale"):
                worker.load_structural_stage(
                    self.slug, self.chapter, self.status, self.helper, changed)

    def test_failed_response_keeps_accepted_segments(self):
        self.once()

        def malformed(_role, _system, _user):
            return json.dumps({"segments": [{"id": "s99999", "translation": "wrong"}],
                               "glossary": [], "style_guide": []})

        with self.assertRaisesRegex(ModelError, "structural translator"):
            self.once(complete=malformed)
        stage = self.book / ".workflow/auto-structural-translation/chapter-000001.json"
        self.assertEqual(len(json.loads(stage.read_text("utf-8"))["segments"]), 6)

    def test_short_translation_repair_requests_only_rejected_source_segment(self):
        source = "One full sentence, not a summary.\n\n" * 22
        self.plan["segments"] = [
            {"id": f"s{i:05d}", "source": source} for i in range(1, 8)
        ]
        calls = []

        def complete(_role, _system, user):
            payload = user.split("\nREJECTED SHORT TRANSLATION")[0]
            selected = json.loads(payload)
            calls.append([item["id"] for item in selected])
            return json.dumps({
                "segments": [{"id": item["id"],
                              "translation": ("x" if len(calls) == 1
                                              and item["id"] == "s00001"
                                              else source)}
                             for item in selected],
                "glossary": [], "style_guide": [],
            })
        result, count, checkpoints = self.once(complete=complete)
        self.assertIsNone(result)
        self.assertEqual(count, 3)
        self.assertEqual(checkpoints, 5)
        self.assertEqual(calls[0], ["s00001", "s00002", "s00003"])
        self.assertEqual(calls[1], ["s00001"])
        self.assertEqual(calls[2], ["s00004", "s00005", "s00006"])
        stage = self.book / ".workflow/auto-structural-translation/chapter-000001.json"
        saved = json.loads(stage.read_text("utf-8"))["segments"]
        self.assertEqual(len(saved), 6)
        self.assertEqual(saved["s00001"], source)

    def test_runtime_public_ref_is_an_immutable_commit(self):
        self.assertRegex(worker.PUBLIC_STRUCTURAL_HELPER_REVISION,
                         r"^[0-9a-f]{40}$")
        self.assertEqual(worker.STRUCTURAL_MODEL_CALLS_PER_CHECKPOINT, 2)
        self.assertEqual(worker.STRUCTURAL_MODEL_SEGMENTS_PER_CALL, 3)



    def test_invalid_json_gets_one_durable_focused_repair_and_single_span_mode(self):
        requests = []

        def model(_role, _system, user):
            original = user.split("\nREJECTED STRUCTURAL OUTPUT:")[0]
            selected = json.loads(original)
            requests.append([item["id"] for item in selected])
            if len(requests) == 1:
                return "not JSON"
            return json.dumps({
                "segments": [{"id": item["id"], "translation": "Перевод " + item["id"]}
                             for item in selected],
                "glossary": [], "style_guide": [],
            }, ensure_ascii=False)

        outcome, calls, checkpoints = self.once(complete=model)
        self.assertIsNone(outcome)
        self.assertEqual((calls, checkpoints), (3, 5))
        self.assertEqual(requests, [
            ["s00001", "s00002", "s00003"], ["s00001"], ["s00002"],
        ])
        self.assertEqual(self.status["model_calls_started"], 3)
        self.assertEqual(self.status["structural_protocol_fallback_units"][
            self.chapter["unit_id"]], self.plan["source_sha256"])
        staged = self.book / ".workflow/auto-structural-translation/chapter-000001.json"
        self.assertEqual(set(json.loads(staged.read_text("utf-8"))["segments"]),
                         {"s00001", "s00002"})
        self.assertFalse((self.book / "translated/001.md").exists())

    def test_transport_error_is_not_reinterpreted_as_json_repair(self):
        with self.assertRaisesRegex(ModelError, "transport"):
            self.once(complete=lambda *_: (_ for _ in ()).throw(
                ModelError("model transport timeout or connection failure")))
        self.assertEqual(self.status["model_calls_started"], 1)
        self.assertNotIn("structural_protocol_fallback_units", self.status)

    def test_large_truncated_candidate_is_detected_before_acceptance(self):
        source = ("Long source paragraph with many important details. " * 4 + "\n\n") * 30
        missing = ("Пропущенная часть.\n\n") * 8
        self.assertTrue(worker.suspicious_translation_coverage(source, missing))


if __name__ == "__main__":
    unittest.main()
