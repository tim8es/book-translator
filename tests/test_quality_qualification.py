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


class IndependentAuditTests(unittest.TestCase):
    def test_strict_boolean_protocol(self):
        for bad in ('{"fact": 1}', '{"fact": true, "extra": true}', '{}'):
            with self.assertRaisesRegex(RuntimeError, "protocol invalid"):
                qual.checked_audit(lambda *args: bad, {"fact": "fact"}, "s", "t")
        self.assertEqual(qual.checked_audit(
            lambda *args: '{"fact": false}', {"fact": "fact"}, "s", "t"), {"fact": False})

    def test_unit_audit_retains_local_failure_and_checks_every_unit(self):
        import json
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapters = []
            for n in range(1, 7):
                (root / f"s{n}").write_text(f"source {n}", encoding="utf-8")
                (root / f"t{n}").write_text(f"target {n}", encoding="utf-8")
                chapters.append({"number": n, "unit_id": f"chapter-{n:06}",
                                 "source_path": f"s{n}", "translation_path": f"t{n}"})
            def complete(role, system, user):
                self.assertEqual(role, "REVIEWER")
                n = int(json.loads(user)["source"].split()[-1])
                keys = qual.UNIT_FACTS.get(n, ()) + ("complete_and_fluent", "dialogue_voice")
                return json.dumps({k: k != "empty_envelope" for k in keys})
            report = {"independent_audit_calls": 1}
            self.assertFalse(qual.audit_units(complete, root, chapters, report))
            self.assertEqual(report["independent_audit_calls"], 7)
            self.assertEqual(len(report["unit_audits"]), 6)
            self.assertFalse(report["unit_audits"][4]["checks"]["empty_envelope"])
            self.assertNotIn("empty_envelope", report["unit_audits"][3]["checks"])
            self.assertEqual(report["unit_audits"][4]["translation_sha256"],
                             hashlib.sha256(b"target 5").hexdigest())
            self.assertNotIn("target 5", json.dumps(report))


class ShortConfirmationQualificationTests(unittest.TestCase):
    def test_final_coverage_requires_exact_accepted_hashes(self):
        chapters = [{"unit_id": "chapter-000001", "translation_acceptance":
                     {"source_sha256": "a" * 64, "translation_sha256": "b" * 64}}]
        decision = {"outcome": "PASS", "binding": dict(chapters[0]["translation_acceptance"])}
        status = {"short_pass_confirmations": {"chapter-000001": decision}}
        self.assertEqual(qual.short_confirmation_coverage(status, chapters), 1)
        decision["binding"]["translation_sha256"] = "c" * 64
        self.assertEqual(qual.short_confirmation_coverage(status, chapters), 0)
        decision["binding"] = dict(chapters[0]["translation_acceptance"])
        decision["outcome"] = "reserved"
        self.assertEqual(qual.short_confirmation_coverage(status, chapters), 0)

    def test_failed_fixture_sample_redacts_before_truncation_and_checks_hashes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = b"Authored synthetic source."
            target = ("x" * 4997 + "fake-secret-value https://example.invalid").encode()
            (root / "s").write_bytes(source)
            (root / "t").write_bytes(target)
            chapters = [{"number": 1, "source_path": "s", "translation_path": "t"}]
            audit = {"number": 1, "checks": {"complete_and_fluent": False},
                     "source_sha256": hashlib.sha256(source).hexdigest(),
                     "translation_sha256": hashlib.sha256(target).hexdigest()}
            report = {"unit_audits": [audit]}
            with patch.dict(os.environ, {"OMNIROUTE_API_KEY": "fake-secret-value"}):
                sample = qual.failed_fixture_samples(root, chapters, report)[0]
            self.assertEqual(len(sample["translation_excerpt"]), 5000)
            self.assertNotIn("fake", sample["translation_excerpt"])
            self.assertTrue(sample["truncated"])
            (root / "t").write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                qual.failed_fixture_samples(root, chapters, report)


class SyntheticFailureDiagnosticsTests(unittest.TestCase):
    def test_model_retry_and_terminal_block_preserve_actual_cause(self):
        report = {}
        status = {"state": "running", "phase": "model-retry",
                  "model_calls_started": 2, "failures": 1,
                  "blocked_unit_id": "chapter-000001",
                  "last_error": "corrector returned unchanged translation and memory",
                  "prompt_context": {"role": "corrector", "correction_mode": "anchored-patches",
                                     "user": "never include this prompt"}}
        qual.collect_fixture_error(report, status, 4)
        status.update(state="blocked", phase="model-blocked", failures=3)
        qual.collect_fixture_error(report, status, 8)
        history = report["synthetic_error_history"]
        self.assertEqual([v["checkpoint"] for v in history], [4, 8])
        self.assertEqual(history[-1]["error"], status["last_error"])
        self.assertEqual(history[-1]["failures"], 3)
        self.assertNotIn("never include this prompt", str(report))

    def test_error_redaction_and_bounded_history(self):
        env = {"OMNIROUTE_API_KEY": "synthetic-secret", "OMNIROUTE_REVIEWER_MODEL": "hidden-route",
               "OTHER_TOKEN": "another-secret"}
        error = "hidden-route synthetic-secret another-secret https://provider.invalid/path Bearer token123 sk-private123"
        with patch.dict(os.environ, env):
            report = {}
            for n in range(20):
                qual.collect_fixture_error(report, {"phase": "model-blocked",
                    "last_error": error, "model_calls_started": 2}, n)
        history = report["synthetic_error_history"]
        self.assertEqual(len(history), 12)
        self.assertEqual(history[0]["checkpoint"], 8)
        for secret in ("hidden-route", "synthetic-secret", "another-secret",
                       "provider.invalid", "token123", "sk-private123"):
            self.assertNotIn(secret, str(history))

    def test_successful_checkpoint_not_recorded(self):
        report = {}
        qual.collect_fixture_error(report, {"phase": "reviewer-completed"}, 1)
        self.assertEqual(report, {})


class AnchoredAuditDiagnosticsTests(unittest.TestCase):
    def test_failed_audit_stays_failed_and_diagnostic_requires_real_quotes(self):
        import hashlib
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = "The box had seven coins."
            target = "В коробке было девять монет."
            (root / "s").write_text(source, encoding="utf-8")
            (root / "t").write_text(target, encoding="utf-8")
            chapters = [{"number": 1, "source_path": "s", "translation_path": "t"}]
            report = {"unit_audits": [{"number": 1, "unit_id": "chapter-000001",
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "translation_sha256": hashlib.sha256(target.encode()).hexdigest(),
                "checks": {"complete_and_fluent": False}}]}
            finding = {"check": "complete_and_fluent", "category": "meaning",
                "source_quote": "seven coins", "target_quote": "девять монет",
                "detail": "Wrong count"}
            qual.explain_failed_unit_audits(lambda *a: json.dumps({"findings": [finding]}),
                                           root, chapters, report)
            self.assertEqual(report["audit_diagnostic_calls"], 1)
            self.assertEqual(report["audit_diagnostics"][0]["status"], "diagnosed")
            self.assertFalse(report["unit_audits"][0]["checks"]["complete_and_fluent"])
            finding["source_quote"] = "invented evidence"
            qual.explain_failed_unit_audits(lambda *a: json.dumps({"findings": [finding]}),
                                           root, chapters, report)
            self.assertEqual(report["audit_diagnostics"][0]["status"], "unavailable")
            qual.explain_failed_unit_audits(lambda *a: '{"findings":[]}', root, chapters, report)
            self.assertEqual(report["audit_diagnostics"][0]["status"], "not-confirmed")
            self.assertFalse(report["unit_audits"][0]["checks"]["complete_and_fluent"])
