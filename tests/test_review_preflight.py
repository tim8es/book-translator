import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_preflight import inspect


class ReviewPreflightTests(unittest.TestCase):
    def test_hash_bound_advisory_warnings_not_quality_pass(self):
        source = b"# Scene\n\nAlex saw 2024 and 2024.\n\n* * *\n"
        translation = "# Сцена\n\nАлекс видел 2024.\n".encode()
        report = inspect(source, translation, "| Original | Translation |\n| --- | --- |\n| Alex | Алекс |\n")
        self.assertTrue(report["advisory_only"])
        self.assertEqual(report["source_sha256"], hashlib.sha256(source).hexdigest())
        self.assertEqual(report["translation_sha256"], hashlib.sha256(translation).hexdigest())
        self.assertEqual([x["kind"] for x in report["warnings"]], ["number_missing", "structure"])

    def test_glossary_warning_is_advisory_and_no_mutation(self):
        original = "The Warden opened the gate.".encode()
        target = "Страж открыл ворота.".encode()
        report = inspect(original, target, "| Original | Translation |\n|---|---|\n| Warden | Хранитель |\n")
        self.assertEqual(report["warnings"][0]["kind"], "glossary_candidate")
        self.assertEqual(original, "The Warden opened the gate.".encode())
        self.assertEqual(target, "Страж открыл ворота.".encode())

    def test_latin_character_glossary_is_flagged_not_enforced(self):
        glossary = (
            "| Original | Translation | Type | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Cade | Cade | character | Recurring character; keep consistent. |\n"
        )
        report = inspect(
            b"Cade and I were friends.",
            "Мы с Кейдом дружили.".encode("utf-8"),
            glossary,
        )
        self.assertEqual(
            [warning["kind"] for warning in report["warnings"]],
            ["glossary_script_risk"],
        )

    def test_latin_brand_and_explicit_character_exception_are_not_flagged(self):
        glossary = (
            "| Original | Translation | Type | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Xbox | Xbox | brand | Retain trademark. |\n"
            "| R2D2 | R2D2 | character | [latin-script-exception] Human approved. |\n"
        )
        report = inspect(
            b"R2D2 plays Xbox.",
            "R2D2 играет на Xbox.".encode("utf-8"),
            glossary,
        )
        self.assertFalse(any(w["kind"] == "glossary_script_risk" for w in report["warnings"]))

    def test_clean_sample_does_not_claim_literary_pass(self):
        report = inspect("Five people spoke.".encode(), "Говорили пять человек.".encode())
        self.assertEqual(report["warnings"], [])
        self.assertNotIn("outcome", report)
        self.assertNotIn("passed", report)


if __name__ == "__main__":
    unittest.main()
