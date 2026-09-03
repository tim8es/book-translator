import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SourceCorpusContractTests(unittest.TestCase):
    def test_orchestrator_requires_batch_corpus_preflight_before_literary_work(self):
        text = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8").lower()

        for phrase in (
            "corpus preflight",
            "scripts/corpus.py restore",
            "restore the complete source corpus in one batch",
            "do not repair missing extracted chapters one at a time",
            "before dispatching literary work",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
