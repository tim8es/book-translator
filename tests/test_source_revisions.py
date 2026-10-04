import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOK_SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
CORPUS_SCRIPT = PROJECT_ROOT / "scripts" / "corpus.py"
WORKFLOW = PROJECT_ROOT / "scripts" / "workflow"
TEMPLATES = PROJECT_ROOT / "docs" / "templates"


class SourceRevisionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW, self.repo / "scripts" / "workflow")
        if TEMPLATES.exists():
            shutil.copytree(TEMPLATES, self.repo / "docs" / "templates")
        (self.repo / ".book-translator-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": "https://github.com/tim8es/book-translator",
                    "requested_ref": "feature/source-revisions-durable-update",
                    "resolved_revision": "source-revision-test",
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "book.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            expect,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def make_source(self, name, chapters):
        path = self.repo / name
        text = "\n\n".join(f"# {title}\n\n{body}" for title, body in chapters) + "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def initialize(self):
        source = self.make_source("book.md", [("One", "Alpha."), ("Two", "Beta.")])
        self.run_cli(
            "extract",
            str(source),
            "--slug",
            "sample",
            "--target-language",
            "ru",
        )
        return self.repo / "books" / "sample"

    def translate_and_review(self, chapter):
        book = self.repo / "books" / "sample"
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        record = progress["chapters"][chapter - 1]
        target = book / record["translation_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# RU {chapter}\n\nПеревод {chapter}.\n", encoding="utf-8")

        translator = f"translator-{chapter}"
        reviewer = f"reviewer-{chapter}"
        self.run_cli("claim", "sample", str(chapter), "--role", "translator", "--session-id", translator, "--json")
        self.run_cli("accept-translation", "sample", str(chapter), "--session-id", translator, "--json")
        self.run_cli("release", "sample", str(chapter), "--session-id", translator, "--json")
        self.run_cli(
            "claim",
            "sample",
            str(chapter),
            "--role",
            "reviewer",
            "--session-id",
            reviewer,
            "--base-commit",
            f"review-{chapter}",
            "--json",
        )
        self.run_cli(
            "review-record",
            "sample",
            str(chapter),
            "--outcome",
            "PASS",
            "--session-id",
            reviewer,
            "--review-commit",
            f"review-{chapter}",
            "--json",
        )
        self.run_cli("release", "sample", str(chapter), "--session-id", reviewer, "--json")
        self.run_cli("accept-review", "sample", str(chapter), "--json")

    def test_extract_creates_initial_source_revision_and_stable_unit_ids(self):
        book = self.initialize()
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        revisions = json.loads((book / "source-revisions.json").read_text(encoding="utf-8"))

        self.assertEqual([c["unit_id"] for c in progress["chapters"]], ["chapter-000001", "chapter-000002"])
        self.assertEqual(metadata["source"]["revision_id"], "source-000001")
        self.assertEqual(revisions["active_revision"], "source-000001")
        self.assertEqual(revisions["next_sequence"], 2)
        snapshot = book / revisions["revisions"][0]["snapshot_path"]
        self.assertTrue(snapshot.is_file())

    def test_inserted_unit_reuses_unchanged_translation_and_review_evidence(self):
        book = self.initialize()
        self.translate_and_review(1)
        self.translate_and_review(2)
        before = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        before_by_title = {c["title"]: c for c in before["chapters"]}

        updated = self.make_source(
            "book-v2.md",
            [("One", "Alpha."), ("New", "Gamma."), ("Two", "Beta.")],
        )
        result = self.run_cli("update-source", "sample", str(updated), "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "promoted")
        self.assertEqual(payload["delta"]["unchanged"], 2)
        self.assertEqual(payload["delta"]["new"], 1)

        after = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        by_title = {c["title"]: c for c in after["chapters"]}
        self.assertEqual([c["number"] for c in after["chapters"]], [1, 2, 3])
        self.assertEqual(by_title["One"]["unit_id"], before_by_title["One"]["unit_id"])
        self.assertEqual(by_title["Two"]["unit_id"], before_by_title["Two"]["unit_id"])
        self.assertEqual(by_title["One"]["translation_path"], before_by_title["One"]["translation_path"])
        self.assertEqual(by_title["Two"]["translation_path"], before_by_title["Two"]["translation_path"])
        self.assertEqual(by_title["One"]["status"], "reviewed")
        self.assertEqual(by_title["Two"]["status"], "reviewed")
        self.assertIn("translation_acceptance", by_title["One"])
        self.assertIn("translation_acceptance", by_title["Two"])
        self.assertEqual(by_title["New"]["status"], "extracted")
        self.assertNotIn("translation_acceptance", by_title["New"])

        reviews = json.loads(self.run_cli("reviews", "sample", "--json").stdout)
        states = {item["chapter_number"]: item["state"] for item in reviews["chapters"]}
        self.assertEqual(states[1], "pass")
        self.assertEqual(states[3], "pass")

    def test_changed_unit_keeps_identity_but_invalidates_translation_and_review(self):
        book = self.initialize()
        self.translate_and_review(1)
        self.translate_and_review(2)
        before = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        old_one = before["chapters"][0]
        old_translation = book / old_one["translation_path"]
        old_translation_bytes = old_translation.read_bytes()

        updated = self.make_source(
            "book-v2.md",
            [("One", "Alpha changed."), ("Two", "Beta.")],
        )
        self.run_cli("update-source", "sample", str(updated), "--json")

        after = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        one, two = after["chapters"]
        self.assertEqual(one["unit_id"], old_one["unit_id"])
        self.assertEqual(one["status"], "extracted")
        self.assertNotIn("translation_acceptance", one)
        self.assertNotEqual(one["source_path"], old_one["source_path"])
        self.assertNotEqual(one["translation_path"], old_one["translation_path"])
        self.assertEqual(old_translation.read_bytes(), old_translation_bytes)
        self.assertEqual(two["status"], "reviewed")
        self.assertTrue((book / one["source_path"]).is_file())

    def test_deletion_is_staged_and_requires_explicit_promotion(self):
        book = self.initialize()
        before = (book / "progress.json").read_bytes()
        updated = self.make_source("book-v2.md", [("One", "Alpha.")])

        result = self.run_cli("update-source", "sample", str(updated), "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "staged_requires_decision")
        self.assertEqual(payload["delta"]["deleted"], 1)
        self.assertEqual((book / "progress.json").read_bytes(), before)

        revision_id = payload["revision_id"]
        blocked = self.run_cli(
            "promote-source-update",
            "sample",
            revision_id,
            "--json",
            expect=1,
        )
        self.assertIn("--allow-deletions", blocked.stderr)

        promoted = self.run_cli(
            "promote-source-update",
            "sample",
            revision_id,
            "--allow-deletions",
            "--json",
        )
        self.assertEqual(json.loads(promoted.stdout)["state"], "promoted")
        after = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(len(after["chapters"]), 1)

    def test_promote_is_idempotent_after_success(self):
        self.initialize()
        updated = self.make_source(
            "book-v2.md",
            [("One", "Alpha."), ("Two", "Beta."), ("Three", "Gamma.")],
        )
        first = json.loads(self.run_cli("update-source", "sample", str(updated), "--json").stdout)
        revision_id = first["revision_id"]
        second = json.loads(
            self.run_cli("promote-source-update", "sample", revision_id, "--json").stdout
        )
        self.assertEqual(second["state"], "already_active")

    def test_source_revision_status_is_machine_readable(self):
        self.initialize()
        result = self.run_cli("source-revisions", "sample", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["active_revision"], "source-000001")
        self.assertEqual(len(payload["revisions"]), 1)


if __name__ == "__main__":
    unittest.main()
