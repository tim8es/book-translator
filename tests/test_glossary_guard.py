import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.glossary_guard import suspicious_untranslated_character_names


class GlossaryGuardTests(unittest.TestCase):
    def test_copied_latin_names_ru_are_suspect(self):
        text = (
            "| Original | Translation | Type | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Cade | Cade | character | recurring |\n"
            "| Dillon | Dillon | character | recurring |\n"
            "| Xbox | Xbox | brand | trademark |\n"
        )
        self.assertEqual(
            suspicious_untranslated_character_names(text, "ru"),
            ["Cade", "Dillon"],
        )
        self.assertEqual(suspicious_untranslated_character_names(text, "en"), [])

    def test_approved_latin_exception_and_transliteration(self):
        text = (
            "| Original | Translation | Type | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| Cade | Кейд | character | inflect as Кейдом |\n"
            "| R2D2 | R2D2 | character | [latin-script-exception] approved |\n"
        )
        self.assertEqual(suspicious_untranslated_character_names(text, "ru"), [])


if __name__ == "__main__":
    unittest.main()
