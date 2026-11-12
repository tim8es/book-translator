import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.glossary_guard import (
    conflicting_glossary_entries, glossary_context, glossary_entries,
    glossary_mismatch_candidates,
)


class GlossaryGuardTests(unittest.TestCase):
    GLOSSARY = (
        "| Original | Translation | Type | Notes |\n"
        "| --- | --- | --- | --- |\n"
        "| Cade | Кейд | character | May inflect as Кейдом |\n"
        "| Dillon | Dillon | character | Intentionally unchanged |\n"
        "| Xbox | Xbox | brand | Retain trademark |\n"
    )

    def test_context_carries_entries_and_language_pair(self):
        data = json.loads(glossary_context(self.GLOSSARY, "en", "ru"))
        self.assertEqual((data["source_language"], data["target_language"]), ("en", "ru"))
        self.assertEqual(data["entries"][0]["translation"], "Кейд")
        self.assertIn("morphology", data["guidance"])
        self.assertEqual(
            json.loads(glossary_context(self.GLOSSARY, "en", "ja"))["entries"][1]["translation"],
            "Dillon",
        )

    def test_no_script_specific_rejection(self):
        self.assertEqual(conflicting_glossary_entries(self.GLOSSARY), [])
        warnings = glossary_mismatch_candidates(
            "Cade met Dillon.", "Кейдом встретился с Диллоном.", self.GLOSSARY
        )
        self.assertEqual([item["kind"] for item in warnings], ["glossary_decision_review"])
        self.assertEqual(warnings[0]["original"], "Dillon")

    def test_contradictory_entries_are_detected_in_any_language(self):
        text = self.GLOSSARY + "| Cade | Caio | character | alternative |\n"
        self.assertEqual(conflicting_glossary_entries(text), ["cade"])

    def test_escaped_pipe_and_same_script(self):
        text = (
            "| Original | Translation | Type | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Io | Io | character | same |\n"
            "| Foo | Foo | title | a \\| b |\n"
        )
        entries = glossary_entries(text)
        self.assertEqual(entries[-1]["notes"], "a | b")


if __name__ == "__main__":
    unittest.main()
