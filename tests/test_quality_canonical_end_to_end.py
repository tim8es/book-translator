"""Synthetic canonical CLI integration: independent arbitration to reviewed EPUB."""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import book_runner as runner


class QualityCanonicalEndToEnd(unittest.TestCase):
    def test_independent_arbitration_preserves_text_and_builds_final_epub(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "state"
            scripts = repo / "scripts"
            scripts.mkdir(parents=True)
            for name in ("book.py", "corpus.py"):
                shutil.copy2(ROOT / "scripts" / name, scripts / name)
            shutil.copytree(ROOT / "scripts/workflow", scripts / "workflow")
            shutil.copy2(ROOT / "agent-manifest.json", repo / "agent-manifest.json")
            revision = "a" * 40
            (repo / ".book-translator-install.json").write_text(json.dumps({
                "schema_version": 1, "canonical_repository": "https://github.com/tim8es/book-translator",
                "requested_ref": revision, "resolved_revision": revision, "install_root": "."}) + "\n")
            source = repo / "source.md"
            source.write_text("# Chapter One\n\nShe closed the old door.\n", "utf-8")
            translation = "# Глава первая\n\nОна закрыла старую дверь.\n"
            status = {"state": "running", "workflow_revision": revision,
                      "correction_rounds": {}, "failures": 0}
            job_id = "d" * 32
            calls = []
            def model(role, system, user):
                calls.append(role)
                if role == "TRANSLATOR":
                    return json.dumps({"translation": translation, "glossary": [], "style_guide": []}, ensure_ascii=False)
                if "INDEPENDENT EDITORIAL ARBITRATION" in system:
                    return json.dumps({"decisions": [{"finding_id": "wording",
                        "verdict": "acceptable_variant",
                        "reason": "The current phrase is idiomatic and fully preserves the source action."}]})
                self.assertEqual(role, "REVIEWER")
                return json.dumps({"evidence_version": 2, "outcome": "CORRECTIONS_REQUIRED",
                    "checks": dict.fromkeys(("coverage", "meaning", "terminology", "style", "structure"), True),
                    "findings": [{"id": "wording", "category": "style", "severity": "minor",
                        "detail": "Reviewer prefers a different but equally natural verb.",
                        "source_anchor": {"quote": "closed the old door", "occurrence": 0},
                        "translation_anchor": {"quote": "закрыла старую дверь", "occurrence": 0},
                        "resolved": False, "resolves": None}], "style_guide": []}, ensure_ascii=False)
            with patch.object(runner, "ROOT", repo), \
                 patch.object(runner, "git", return_value="b" * 40), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "model_call_limit", return_value=100), \
                 patch.object(runner, "complete", side_effect=model):
                runner.cli("extract", str(source), "--slug", "sample", "--target-language", "ru")
                sealed = subprocess.run([sys.executable, str(scripts / "corpus.py"), "seal", "sample"],
                                        cwd=repo, capture_output=True, text=True)
                self.assertEqual(sealed.returncode, 0, msg=sealed.stderr)
                runner.verify("sample")
                chapter = runner.chapter_data("sample")[2][0]
                runner.claim(job_id, "sample", status, "translator", chapter)
                runner.process_claim(job_id, "sample", status, ROOT)
                chapter = runner.chapter_data("sample")[2][0]
                runner.claim(job_id, "sample", status, "reviewer", chapter)
                runner.process_claim(job_id, "sample", status, ROOT)
                _, ledger, chapters = runner.chapter_data("sample")
                self.assertEqual(chapters[0]["status"], "reviewed")
                self.assertEqual(ledger["records"][-1]["outcome"], "PASS")
                self.assertEqual(ledger["records"][-1]["quality_report"]["adjudications"][0]["decision"],
                                 "acceptable_variant")
                self.assertEqual((repo / "books/sample" / chapters[0]["translation_path"]).read_text("utf-8"),
                                 translation)
                runner.finalize(job_id, "sample", status)
                self.assertEqual(status["state"], "done")
                self.assertTrue((repo / status["artifact_path"]).is_file())
                self.assertEqual(calls, ["TRANSLATOR", "REVIEWER", "REVIEWER"])


if __name__ == "__main__":
    unittest.main()
