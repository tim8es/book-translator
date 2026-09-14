import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureConsolidationTests(unittest.TestCase):
    def test_repository_contains_one_current_workflow_surface(self):
        forbidden_runtime = [
            ROOT / "scripts" / "workflow_v2" / "migration_journal.py",
            ROOT / "scripts" / "workflow_v2" / "migrations.py",
            ROOT / "scripts" / "workflow_v2" / "migrations_cli.py",
        ]
        self.assertEqual(
            [path.relative_to(ROOT).as_posix() for path in forbidden_runtime if path.exists()],
            [],
        )

        historical_design_docs = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "docs").glob("WORKFLOW_V2_*.md")
        )
        self.assertEqual(historical_design_docs, [])

    def test_canonical_contract_does_not_offer_legacy_or_upgrade_runtime(self):
        orchestration = (ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        self.assertNotIn("legacy contract", orchestration.lower())
        self.assertNotIn("workflow upgrade", orchestration.lower())
        self.assertNotIn("migration journal", orchestration.lower())


if __name__ == "__main__":
    unittest.main()
