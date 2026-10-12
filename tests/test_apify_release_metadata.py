"""Static publication package checks, runnable on Python 3.10+ without Apify credentials."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTOR = ROOT / ".actor"


class ApifyPublishingMetadataTests(unittest.TestCase):
    def read_json(self, name):
        return json.loads((ACTOR / name).read_text(encoding="utf-8"))

    def test_definition_includes_all_store_output_schemas(self):
        definition = self.read_json("actor.json")
        self.assertEqual(definition["actorSpecification"], 1)
        self.assertEqual(definition["version"], "0.1")
        self.assertTrue(definition["name"])
        self.assertTrue(definition["title"])
        self.assertTrue(definition["description"])
        self.assertFalse(definition["usesStandbyMode"])
        self.assertGreaterEqual(definition["defaultMemoryMbytes"], definition["minMemoryMbytes"])
        self.assertLessEqual(definition["defaultMemoryMbytes"], definition["maxMemoryMbytes"])
        names = [definition["input"], definition["output"], definition["readme"]]
        names += list(definition["storages"].values())
        for name in names:
            path = (ACTOR / name).resolve()
            self.assertTrue(path.is_relative_to(ACTOR.resolve()))
            self.assertTrue(path.is_file(), name)

    def test_upload_input_and_output_format_are_consistent(self):
        spec = self.read_json("INPUT_SCHEMA.json")
        props = spec["properties"]
        self.assertEqual(spec["schemaVersion"], 1)
        self.assertEqual(props["bookFiles"]["editor"], "fileupload")
        self.assertEqual(props["bookFiles"]["minItems"], 1)
        self.assertEqual(props["bookFiles"]["maxItems"], 1)
        self.assertEqual(set(props["outputFormat"]["enum"]), {"epub", "markdown", "both"})
        self.assertIn(props["outputFormat"]["default"], props["outputFormat"]["enum"])
        self.assertIn("bookFiles", spec["required"])
        self.assertIn("targetLanguage", spec["required"])
        self.assertIn("paid Apify plan", spec["description"])

    def test_output_keys_and_storage_collections_match_runtime(self):
        output = self.read_json("OUTPUT_SCHEMA.json")
        kv = self.read_json("key_value_store_schema.json")
        ds = self.read_json("dataset_schema.json")
        self.assertEqual(output["actorOutputSchemaVersion"], 1)
        self.assertEqual(kv["actorKeyValueStoreSchemaVersion"], 1)
        self.assertEqual(ds["actorSpecification"], 1)
        self.assertIn("overview", ds["views"])
        collections = kv["collections"]
        self.assertEqual(collections["books"]["keyPrefix"], "OUTPUT_")
        self.assertEqual(collections["chapters"]["keyPrefix"], "CHAPTER_")
        self.assertEqual(collections["summary"]["key"], "SUMMARY")
        source = (ROOT / "actor_src/main.py").read_text(encoding="utf-8")
        for key in ("OUTPUT_EPUB", "OUTPUT_MARKDOWN", "CHAPTER_", "SUMMARY"):
            self.assertIn(key, source)

    def test_public_readme_is_truthful_and_detailed(self):
        readme = (ACTOR / "README.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(readme.split()), 300)
        for phrase in ("EPUB", "Markdown", "TXT", "PDF / DOCX",
                       "translation-1k-words", "book-started", "paid Apify plan",
                       "Privacy and data retention", "not a guarantee"):
            self.assertIn(phrase, readme)
        self.assertNotIn("BOOK_TRANSLATOR_LLM_API_KEY=", readme)


if __name__ == "__main__":
    unittest.main()
