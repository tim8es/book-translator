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
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.epub_output import build_epub_bytes, validate_epub_bytes


REVISION = "0123456789abcdef"


class WorkflowV2EpubReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        (self.repo / ".book-translator-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": "https://github.com/tim8es/book-translator",
                    "requested_ref": "refactor/workflow-engine-v2",
                    "resolved_revision": REVISION,
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

    def status(self, slug="sample", *, expect=0):
        result = self.run_cli("build-status", slug, "--format", "epub", "--json", expect=expect)
        return json.loads(result.stdout)

    def initialize_book(self, slug="sample", *, chapters=1):
        source = self.repo / f"{slug}.md"
        sections = []
        for number in range(1, chapters + 1):
            sections.append(f"# Chapter {number}\n\nSource {number}.\n")
        source.write_text("\n".join(sections), encoding="utf-8")
        self.run_cli(
            "extract",
            str(source),
            "--slug",
            slug,
            "--target-language",
            "ru",
        )
        book = self.repo / "books" / slug
        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual(len(progress["chapters"]), chapters)
        for chapter in progress["chapters"]:
            translation = book / chapter["translation_path"]
            translation.parent.mkdir(parents=True, exist_ok=True)
            translation.write_text(
                f"# Глава {chapter['number']}\n\nПеревод {chapter['number']}.\n",
                encoding="utf-8",
            )
            chapter["status"] = "translated"
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return book

    def initialize_final_book(self, slug="sample", *, chapters=1):
        book = self.initialize_book(slug, chapters=chapters)
        for chapter in range(1, chapters + 1):
            session = f"reviewer-{chapter}"
            self.run_cli(
                "claim",
                slug,
                str(chapter),
                "--role",
                "reviewer",
                "--session-id",
                session,
                "--base-commit",
                "review-dispatch",
                "--json",
            )
            self.run_cli(
                "review-record",
                slug,
                str(chapter),
                "--outcome",
                "PASS",
                "--session-id",
                session,
                "--review-commit",
                f"review-commit-{chapter}",
                "--json",
            )
            self.run_cli("release", slug, str(chapter), "--session-id", session, "--json")
        self.run_cli("finalize", slug, "--session-id", "finalizer-a", "--json")
        return book

    def build(self, slug="sample"):
        self.run_cli("build", slug, "--format", "epub")
        book = self.repo / "books" / slug
        return book / "output" / f"{slug}.epub", book / "output" / "manifest.json"

    def replacement_epub(self, book, slug="sample"):
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        units = []
        for chapter in progress["chapters"]:
            units.append(
                {
                    "number": chapter["number"],
                    "title": chapter["title"],
                    "slug": chapter["slug"],
                    "markdown": (book / chapter["translation_path"]).read_text(encoding="utf-8"),
                }
            )
        content = build_epub_bytes(
            book_slug=slug,
            title="Interrupted replacement artifact",
            author=metadata.get("author"),
            language=metadata["target_language"],
            units=units,
            fingerprint="f" * 64,
            cover=None,
        )
        validate_epub_bytes(content, expected_unit_count=len(units))
        return content

    def test_incomplete_final_build_preserves_existing_artifact_and_manifest(self):
        book = self.initialize_final_book()
        artifact, manifest = self.build()
        before_artifact = artifact.read_bytes()
        before_manifest = manifest.read_bytes()

        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        progress["chapters"][0]["status"] = "translated"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        failed = self.run_cli("build", "sample", "--format", "epub", expect=1)
        self.assertIn("final EPUB requires reviewed chapters", failed.stderr)
        self.assertEqual(artifact.read_bytes(), before_artifact)
        self.assertEqual(manifest.read_bytes(), before_manifest)

    def test_crash_after_artifact_replace_is_detected_and_clean_rerun_repairs_output(self):
        book = self.initialize_final_book()
        artifact, manifest = self.build()
        original_artifact = artifact.read_bytes()
        original_manifest = manifest.read_bytes()

        replacement = self.replacement_epub(book)
        self.assertNotEqual(replacement, original_artifact)
        artifact.write_bytes(replacement)
        self.assertEqual(manifest.read_bytes(), original_manifest)

        interrupted = self.status(expect=1)
        self.assertEqual(interrupted["state"], "invalid")

        self.run_cli("build", "sample", "--format", "epub")
        repaired = self.status()
        self.assertEqual(repaired["state"], "current")
        self.assertEqual(artifact.read_bytes(), original_artifact)
        self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_relevant_metadata_order_cover_translation_and_review_changes_are_stale(self):
        cases = ("metadata", "order", "cover", "translation", "review")
        for case in cases:
            with self.subTest(case=case):
                slug = f"case-{case}"
                chapters = 2 if case == "order" else 1
                book = self.initialize_final_book(slug, chapters=chapters)
                self.build(slug)
                self.assertEqual(self.status(slug)["state"], "current")

                if case == "metadata":
                    path = book / "metadata.json"
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data["title"] = "Relevant metadata change"
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                elif case == "order":
                    progress_path = book / "progress.json"
                    progress = json.loads(progress_path.read_text(encoding="utf-8"))
                    progress["chapters"] = list(reversed(progress["chapters"]))
                    progress_path.write_text(
                        json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    manifest_path = book / "source-manifest.json"
                    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    source_manifest["extracted"] = list(reversed(source_manifest["extracted"]))
                    manifest_path.write_text(
                        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                elif case == "cover":
                    (book / "cover.png").write_bytes(b"PNG-COVER-V1")
                    path = book / "metadata.json"
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data["cover_path"] = "cover.png"
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                elif case == "translation":
                    progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
                    translation = book / progress["chapters"][0]["translation_path"]
                    translation.write_text("# Изменено\n\nНовый перевод.\n", encoding="utf-8")
                else:
                    self.run_cli(
                        "claim",
                        slug,
                        "1",
                        "--role",
                        "reviewer",
                        "--session-id",
                        "reviewer-change",
                        "--base-commit",
                        "review-change",
                        "--json",
                    )
                    self.run_cli(
                        "review-record",
                        slug,
                        "1",
                        "--outcome",
                        "CORRECTIONS_REQUIRED",
                        "--session-id",
                        "reviewer-change",
                        "--review-commit",
                        "review-change",
                        "--json",
                    )
                    self.run_cli("release", slug, "1", "--session-id", "reviewer-change", "--json")

                changed = self.status(slug)
                self.assertEqual(changed["state"], "stale", changed)

    def test_semantically_duplicate_pass_evidence_does_not_make_output_stale(self):
        self.initialize_final_book()
        self.build()
        before = self.status()
        self.assertEqual(before["state"], "current")

        self.run_cli(
            "claim",
            "sample",
            "1",
            "--role",
            "reviewer",
            "--session-id",
            "reviewer-duplicate",
            "--base-commit",
            "duplicate-pass",
            "--json",
        )
        self.run_cli(
            "review-record",
            "sample",
            "1",
            "--outcome",
            "PASS",
            "--session-id",
            "reviewer-duplicate",
            "--review-commit",
            "duplicate-pass",
            "--json",
        )
        self.run_cli("release", "sample", "1", "--session-id", "reviewer-duplicate", "--json")

        after = self.status()
        self.assertEqual(after["state"], "current", after)

    def test_unrelated_git_commit_does_not_make_output_stale_or_rewrite_manifest(self):
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Workflow Tests"], cwd=self.repo, check=True)

        self.initialize_final_book()
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.repo, check=True, capture_output=True)
        artifact, manifest = self.build()
        before_artifact = artifact.read_bytes()
        before_manifest = manifest.read_bytes()
        before_stat = (artifact.stat().st_mtime_ns, manifest.stat().st_mtime_ns)

        (self.repo / "UNRELATED.txt").write_text("unrelated\n", encoding="utf-8")
        subprocess.run(["git", "add", "UNRELATED.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "unrelated"], cwd=self.repo, check=True, capture_output=True)

        self.assertEqual(self.status()["state"], "current")
        self.run_cli("build", "sample", "--format", "epub")
        self.assertEqual(artifact.read_bytes(), before_artifact)
        self.assertEqual(manifest.read_bytes(), before_manifest)
        self.assertEqual((artifact.stat().st_mtime_ns, manifest.stat().st_mtime_ns), before_stat)

    def test_identical_successful_rebuild_is_byte_and_write_idempotent(self):
        self.initialize_final_book()
        artifact, manifest = self.build()
        before_artifact = artifact.read_bytes()
        before_manifest = manifest.read_bytes()
        before_stat = (artifact.stat().st_mtime_ns, manifest.stat().st_mtime_ns)

        result = self.run_cli("build", "sample", "--format", "epub")
        self.assertIn("unchanged", result.stdout)
        self.assertEqual(artifact.read_bytes(), before_artifact)
        self.assertEqual(manifest.read_bytes(), before_manifest)
        self.assertEqual((artifact.stat().st_mtime_ns, manifest.stat().st_mtime_ns), before_stat)
        self.assertEqual(self.status()["state"], "current")


if __name__ == "__main__":
    unittest.main()
