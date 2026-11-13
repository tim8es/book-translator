"""Regression cases for the canonical correction protocol."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow.correction_protocol import (
    CorrectionProtocolError, failure_code, repair_guidance, repair_spec,
    validate_intent, validate_replacement,
)


class CorrectionProtocolTests(unittest.TestCase):
    def test_review_category_does_not_determine_anchor_type(self):
        reviewed = {"id": "omission-1", "resolved": False,
                    "category": "omission",
                    "translation_anchor": {"quote": "Важный текст", "occurrence": 0}}
        genuinely_missing = {"id": "omission-2", "resolved": False,
                             "category": "omission", "translation_anchor": None}
        spec = repair_spec([reviewed, genuinely_missing])
        self.assertEqual(spec["items"][0]["required_keys"],
                         ["finding_id", "operation", "replacement"])
        self.assertEqual(spec["items"][1]["required_keys"],
                         ["finding_id", "operation", "replacement",
                          "insertion_anchor"])
        self.assertTrue(spec["items"][0]["reviewer_bound"])
        self.assertFalse(spec["items"][1]["reviewer_bound"])

    def test_distinct_focused_error_classes(self):
        f = {"id": "omission-1", "resolved": False,
             "category": "omission", "translation_anchor": None}
        patch = {"finding_id": "omission-1", "operation": "insert_before",
                 "insertion_anchor": {"quote": "перед", "occurrence": 0}}
        for val, code in [(None, "not_a_string"), ("", "empty"),
                          (" " * 2, "empty"), ("x" * 10_001, "too_large")]:
            with self.subTest(code=code):
                with self.assertRaises(CorrectionProtocolError) as exc:
                    validate_replacement({**patch, "replacement": val}, f)
                self.assertEqual(exc.exception.code, code)
        self.assertEqual(validate_replacement(
            {**patch, "replacement": "сохранённый фрагмент"}, f),
            "сохранённый фрагмент")

    def test_reviewed_addition_can_delete_only_using_replace(self):
        f = {"id": "addition-1", "resolved": False, "category": "addition",
             "translation_anchor": {"quote": "лишнее", "occurrence": 0}}
        patch = {"finding_id": f["id"], "operation": "replace",
                 "replacement": ""}
        validate_intent(patch, f)
        with self.assertRaises(CorrectionProtocolError):
            validate_intent({**patch, "operation": "insert_before"}, f)

    def test_wrong_address_is_never_used(self):
        f = {"id": "meaning-1", "resolved": False, "category": "meaning",
             "translation_anchor": {"quote": "старое", "occurrence": 0}}
        base = {"finding_id": "meaning-1", "operation": "replace",
                "replacement": "точное"}
        validate_intent(base, f)
        validate_intent({**base, "insertion_anchor": f["translation_anchor"]}, f)
        for key in ("target_anchor", "insertion_anchor"):
            with self.assertRaises(CorrectionProtocolError) as caught:
                validate_intent({**base, key: {"quote": "постороннее",
                                               "occurrence": 0}}, f)
            self.assertEqual(caught.exception.code, "model_changed_reviewer_anchor")

    def test_unclassified_transport_failures_cannot_be_retried_as_patch_repair(self):
        self.assertIsNone(failure_code("model HTTP status 500"))
        self.assertIsNone(failure_code("canonical corpus preflight failed"))
        self.assertEqual(failure_code("corrector replacement is empty or too large"),
                         "invalid_replacement")
        self.assertEqual(failure_code("corrector returned no actionable patches"),
                         "missing_patches")

    def test_repair_instruction_sanitized_and_source_bound(self):
        f = {"id": "omission-1", "resolved": False, "category": "omission",
             "translation_anchor": None}
        prompt = json.loads(repair_guidance(
            [f], "corrector replacement is empty or too large"))
        self.assertEqual(prompt["previous_response_rejected"], "invalid_replacement")
        self.assertEqual(prompt["items"][0]["operation"],
                         "insert_before|insert_after")
        self.assertEqual(prompt["max_replacement_characters"], 10_000)
        self.assertNotIn("original-source text", prompt["retry_instruction"])
        with self.assertRaises(CorrectionProtocolError):
            repair_guidance([f], "untrusted unknown failure")

    def test_bounded_and_unique_findings(self):
        f = {"id": "meaning-1", "resolved": False, "translation_anchor": None}
        with self.assertRaises(CorrectionProtocolError):
            repair_spec([])
        with self.assertRaises(CorrectionProtocolError):
            repair_spec([f, f])
        with self.assertRaises(CorrectionProtocolError):
            repair_spec([{"id": "meaning-1", "resolved": True,
                         "translation_anchor": None}])

if __name__ == "__main__":
    unittest.main()
