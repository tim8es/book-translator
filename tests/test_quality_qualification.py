"""Offline tests for the authored, isolated qualification source."""
from pathlib import Path
import sys
import os
import shutil
from unittest.mock import patch
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import quality_qualification as qual
import book_runner


class QualificationFixtureTests(unittest.TestCase):
    def test_fixture_identity_and_source_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.epub"
            qual.fixture(path)
            self.assertEqual(book_runner.epub_identity(path), (qual.TITLE, qual.AUTHOR))
            with ZipFile(path) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(z.namelist()[0], "mimetype")
                self.assertEqual(z.read("mimetype"), b"application/epub+zip")
                c1 = z.read("OEBPS/c1.xhtml").decode()
                for phrase in qual.PHRASES:
                    self.assertEqual(c1.count(phrase), 1)
                self.assertIn("Nobody answered.", z.read("OEBPS/c2.xhtml").decode())

    def test_real_pinned_cli_initializes_and_seeds_disposable_epub_without_models(self):
        self.assert_canonical_seed("smoke", 2, {1})

    def test_extended_fixture_initializes_and_seeds_three_units_without_models(self):
        self.assert_canonical_seed("extended", 6, {1, 3, 5})

    def assert_canonical_seed(self, profile, total, seed_numbers):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "state"
            remote = parent / "remote.git"
            engine = parent / "engine"
            # Use the same canonical code and real Git, with a local synthetic
            # immutable engine checkout. No network or provider calls.
            engine.mkdir()
            shutil.copytree(ROOT / "scripts", engine / "scripts")
            shutil.copy2(ROOT / "agent-manifest.json", engine / "agent-manifest.json")
            shutil.copytree(ROOT / "docs", engine / "docs")
            shutil.copy2(ROOT / "AGENTS.md", engine / "AGENTS.md")
            (engine / "books").mkdir()
            (engine / "books/.gitkeep").touch()
            qual.git(engine, "init", "-b", "main")
            qual.git(engine, "config", "user.name", "Qualification")
            qual.git(engine, "config", "user.email", "qualification@example.invalid")
            qual.git(engine, "add", "-A")
            qual.git(engine, "commit", "-m", "Test canonical engine")
            revision = qual.git(engine, "rev-parse", "HEAD")
            job = "a" * 32
            qual.create_state(root, remote, revision, job, profile)
            self.assertTrue((root / "books").is_dir())
            def install(requested):
                self.assertEqual(requested, revision)
                os.environ["BOOK_TRANSLATOR_PINNED_ENGINE"] = str(engine)
                os.environ["BOOK_TRANSLATOR_PINNED_REVISION"] = revision
                return engine, revision
            with patch.object(book_runner, "ROOT", root), \
                 patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}), \
                 patch.object(book_runner, "complete", side_effect=AssertionError("paid call")):
                book_runner.admit_pi_job(job)
                install(revision)
                request = book_runner.request_for(job)
                title, author = book_runner.epub_identity(root / "intake/auto" / job / "source.epub")
                slug, old = book_runner.locate_book(title, author, "source.epub", "ru",
                    source_epub=root / "intake/auto" / job / "source.epub",
                    source_sha256=request["source_sha256"])
                self.assertFalse(old)
                status = book_runner.init(job, {**request, "title": title, "author": author},
                                          slug, revision)
                status["source_sha256"] = request["source_sha256"]
                book_runner.checkpoint(job, status, slug)
                self.assertEqual(status["phase"], "initialized")
                with patch.object(book_runner, "install_engine", side_effect=install):
                    seeded = qual.seed(root, job, status, revision, profile)
                _, _, chapters = book_runner.chapter_data(slug)
                self.assertEqual(len(chapters), total)
                seeded_ids = [seeded] if isinstance(seeded, str) else seeded
                self.assertEqual(seeded_ids, [ch["unit_id"] for ch in chapters if ch["number"] in seed_numbers])
                for ch in chapters:
                    self.assertEqual(ch["status"], "translated" if ch["number"] in seed_numbers else "extracted")
                self.assertEqual(status.get("model_calls_started", 0), 0)
                self.assertEqual(qual.git(root, "rev-parse", "HEAD"), qual.git(root, "rev-parse", "origin/main"))

    def test_extended_fixture_has_six_units_and_three_seed_sets(self):
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.epub"
            qual.fixture(path, "extended")
            self.assertEqual(book_runner.epub_identity(path), (qual.TITLE + " Extended", qual.AUTHOR))
            with ZipFile(path) as z:
                self.assertIsNone(z.testzip())
                package = ET.fromstring(z.read("OEBPS/book.opf"))
                ns = {"opf": "http://www.idpf.org/2007/opf"}
                self.assertEqual(len(package.findall("opf:spine/opf:itemref", ns)), 6)
                for n, pairs in qual.extended.SEEDED.items():
                    doc = ET.fromstring(z.read(f"OEBPS/c{n}.xhtml"))
                    text = " ".join(doc.itertext())
                    for original, bad in pairs:
                        self.assertEqual(text.count(original), 1)
                        self.assertNotEqual(original, bad)
                self.assertEqual(set(qual.extended.SEEDED), {1, 3, 5})
                self.assertEqual(set(qual.extended.FRESH), {2, 4, 6})
                self.assertEqual(len(qual.extended.AUDIT), 11)
