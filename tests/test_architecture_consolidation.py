import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureConsolidationTests(unittest.TestCase):
    def test_repository_contains_one_current_workflow_surface(self):
        current_runtime = ROOT / "scripts" / "workflow"
        versioned_runtime = ROOT / "scripts" / "workflow_v2"
        self.assertTrue(current_runtime.is_dir(), "current runtime must live at scripts/workflow")
        self.assertFalse(versioned_runtime.exists(), "version-era scripts/workflow_v2 namespace must be removed")

        historical_design_docs = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "docs").glob("WORKFLOW_V2_*.md")
        )
        self.assertEqual(historical_design_docs, [])

    def test_current_runtime_has_no_legacy_schema_api(self):
        runtime = ROOT / "scripts" / "workflow"
        if not runtime.exists():
            runtime = ROOT / "scripts" / "workflow_v2"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(runtime.glob("*.py"))
        )
        for forbidden in (
            "LEGACY_COMPATIBLE_KINDS",
            "allow_legacy",
            "legacy: bool",
            "install_source_schema_extensions",
            "install_parallel_schema_extensions",
        ):
            self.assertNotIn(forbidden, source)

    def test_cli_and_architecture_use_unversioned_runtime_name(self):
        book_cli = (ROOT / "scripts" / "book.py").read_text(encoding="utf-8")
        corpus_cli = (ROOT / "scripts" / "corpus.py").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        for text in (book_cli, corpus_cli, architecture):
            self.assertNotIn("workflow_v2", text)
            self.assertNotIn("Workflow v2", text)

    def test_canonical_contract_does_not_offer_legacy_or_upgrade_runtime(self):
        orchestration = (ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        self.assertNotIn("legacy contract", orchestration.lower())
        self.assertNotIn("workflow upgrade", orchestration.lower())
        self.assertNotIn("migration journal", orchestration.lower())


if __name__ == "__main__":
    unittest.main()
