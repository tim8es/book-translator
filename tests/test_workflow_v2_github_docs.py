from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkflowV2GitHubDocumentationTests(unittest.TestCase):
    def test_orchestration_declares_github_backend_without_actions_dependency(self):
        text = (ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertIn("github api storage", lowered)
        self.assertIn("github actions", lowered)
        self.assertIn("not required", lowered)
        self.assertIn("re-read", lowered)
        self.assertRegex(lowered, r"blind(?:ly)? retr(?:y|ied)")

    def test_setup_declares_capabilities_scope_and_credential_non_persistence(self):
        text = (ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")
        lowered = text.lower()
        for required in (
            "repository",
            "branch",
            "root prefix",
            "contents",
            "tree",
            "blob",
            "write",
            "credential",
            "never persisted",
            "github actions",
        ):
            with self.subTest(required=required):
                self.assertIn(required, lowered)

    def test_translation_contract_contains_no_github_transport_instructions(self):
        text = (ROOT / "docs" / "TRANSLATION.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("githubrestclient", text)
        self.assertNotIn("github api storage", text)
        self.assertNotIn("x-github-api-version", text)


if __name__ == "__main__":
    unittest.main()
