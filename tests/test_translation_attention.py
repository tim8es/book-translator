"""Canonical untranslated-unit attention queue, CAS and resume regressions."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow.attention import (
    AttentionError, AttentionManager, active_attention, validate_attention,
)
from workflow.status import StatusResolver
from workflow.storage import StorageNotFound, StorageVersionConflict


SOURCE = "a" * 64
CHAPTER = "chapter-000001"
REVISION = "b" * 40


class MemoryStorage:
    def __init__(self):
        self.bytes = None
        self.version = 0

    def read(self, path):
        if self.bytes is None:
            raise StorageNotFound("missing")
        return SimpleNamespace(content=self.bytes, version=str(self.version))

    def create_if_absent(self, path, data):
        if self.bytes is not None:
            raise StorageVersionConflict("occupied")
        self.bytes = data
        self.version += 1

    def write_if_version(self, path, data, previous):
        if str(self.version) != previous:
            raise StorageVersionConflict("stale")
        self.bytes = data
        self.version += 1


def untranslated(source=SOURCE):
    return SimpleNamespace(
        state="untranslated", unit_id=CHAPTER,
        source_sha256=source, translation_sha256=None,
        current_record=None,
    )


class TranslationAttentionTests(unittest.TestCase):
    def setUp(self):
        self.storage = MemoryStorage()
        self.manager = AttentionManager(SimpleNamespace(storage=self.storage))
        self.chapter = {"number": 1, "unit_id": CHAPTER, "status": "extracted"}

    def test_source_bound_untranslated_unit_skips_and_is_not_pass(self):
        entry = self.manager.defer_translation(
            REVISION, untranslated(), reason="model-output-exhausted")
        self.assertEqual(entry["kind"], "translation")
        self.assertNotIn("translation_sha256", entry)
        self.assertNotIn("review_record_id", entry)
        document, _ = self.manager.load(REVISION)
        self.assertEqual(
            active_attention(document["entries"], [untranslated()], [self.chapter]),
            [CHAPTER])
        self.assertEqual(self.manager.defer_translation(
            REVISION, untranslated(), reason="model-output-exhausted"), entry)
        self.assertEqual(self.storage.version, 1)

    def test_changed_source_and_accepted_translation_invalidate_skip(self):
        self.manager.defer_translation(
            REVISION, untranslated(), reason="model-output-exhausted")
        document, _ = self.manager.load(REVISION)
        self.assertEqual(active_attention(
            document["entries"], [untranslated("c" * 64)], [self.chapter]), [])
        translated = SimpleNamespace(state="missing", unit_id=CHAPTER,
            source_sha256=SOURCE, translation_sha256="d" * 64, current_record=None)
        self.assertEqual(active_attention(
            document["entries"], [translated],
            [{**self.chapter, "status": "translated"}]), [])

    def test_invalid_entries_and_review_legacy_preserved(self):
        entry = self.manager.defer_translation(
            REVISION, untranslated(), reason="model-coverage-exhausted")
        document, _ = self.manager.load(REVISION)
        for update in ({"source_sha256": "bad"}, {"review_record_id": "a" * 32},
                       {"kind": "review"}, {"reason": "transport-timeout"}):
            broken = json.loads(json.dumps(document))
            broken["entries"][0].update(update)
            with self.subTest(update=update), self.assertRaises(AttentionError):
                validate_attention(broken, REVISION)
        review = {
            "unit_id": "chapter-000002", "source_sha256": SOURCE,
            "translation_sha256": "e" * 64, "review_record_id": "f" * 32,
            "reason": "correction-limit", "created_at": entry["created_at"],
        }
        document["entries"].append(review)
        self.assertEqual(
            len(validate_attention(document, REVISION)["entries"]), 2)

    def test_explicit_retry_is_hash_bound_and_compare_and_swap(self):
        self.manager.defer_translation(REVISION, untranslated(), reason="operator-attention")
        with self.assertRaisesRegex(AttentionError, "stale"):
            self.manager.retry(REVISION, untranslated("c" * 64))
        self.assertTrue(self.manager.retry(REVISION, untranslated()))
        self.assertFalse(self.manager.retry(REVISION, untranslated()))
        self.assertEqual(self.manager.load(REVISION)[0]["entries"], [])

    def test_rejects_unreviewed_evidence_forgery(self):
        with self.assertRaisesRegex(AttentionError, "untranslated"):
            self.manager.defer_translation(
                REVISION,
                SimpleNamespace(state="pass", unit_id=CHAPTER,
                                source_sha256=SOURCE, translation_sha256=None),
                reason="model-output-exhausted")
        with self.assertRaisesRegex(AttentionError, "unsupported"):
            self.manager.defer_translation(
                REVISION, untranslated(), reason="transport-timeout")

    def test_canonical_resume_skips_only_matching_deferred_unit(self):
        resolver = object.__new__(StatusResolver)
        resolver._context = lambda operation, unit, status: {"operation": operation}
        status = {
            "valid": True, "finalization": {"active": False}, "claims": [],
            "units": [
                {"unit_id": CHAPTER, "chapter_number": 1,
                 "lifecycle": "extracted", "review": "untranslated"},
                {"unit_id": "chapter-000002", "chapter_number": 2,
                 "lifecycle": "extracted", "review": "untranslated"},
            ],
            "needs_attention": [CHAPTER],
        }
        choice = resolver.resume(status)
        self.assertEqual((choice["operation"], choice["unit_id"]),
                         ("translate", "chapter-000002"))
        self.assertEqual(resolver.resume(status, parallel=2)["assignments"][0]["unit_id"],
                         "chapter-000002")
        status["units"][1]["lifecycle"] = "reviewed"
        self.assertEqual(resolver.resume(status)["operation"], "needs_attention")
        self.assertEqual(resolver.resume(status)["units"], [CHAPTER])
        status["needs_attention"] = []
        self.assertEqual(resolver.resume(status)["operation"], "translate")

    def test_invalid_preflight_preempts_attention_queue(self):
        resolver = object.__new__(StatusResolver)
        resolver._context = lambda operation, unit, status: {}
        result = resolver.resume({
            "valid": False, "errors": ["source checksum mismatch"],
            "units": [], "needs_attention": [CHAPTER],
        })
        self.assertEqual(result["operation"], "blocked")
        self.assertEqual(result["reason"], "preflight_failed")


if __name__ == "__main__":
    unittest.main()
