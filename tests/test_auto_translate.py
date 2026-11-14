"""Safety and contract checks for the optional private-only automated runtime."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker
from omniroute import ModelError, complete, config, parse_review


class AutomatedBookTests(unittest.TestCase):
    def test_book_key_and_selection_are_book_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            intake = root / "intake" / "auto"
            intake.mkdir(parents=True)
            (root / "automation" / "status").mkdir(parents=True)
            first, second = "a" * 32, "b" * 32
            for job_id, slug in ((first, "novel-a"), (second, "novel-b")):
                folder = intake / job_id
                folder.mkdir()
                worker.store_json(folder / "request.json", {
                    "job_id": job_id, "source_sha256": job_id * 2,
                    "target_language": "ru"})
                worker.store_json(root / "automation" / "status" / (job_id + ".json"), {
                    "state": "paused", "book_slug": slug})
                control = root / "books" / slug / ".workflow" / "auto-control.json"
                worker.store_json(control, {"schema_version": 1, "mode": "chatops"})
            with patch.object(worker, "ROOT", root):
                self.assertEqual(worker.book_key(first), "novel-a")
                self.assertEqual(worker.book_key(second), "novel-b")
                self.assertEqual(worker.select(None), "")
                control = root / "books" / "novel-b" / ".workflow" / "auto-control.json"
                worker.store_json(control, {"schema_version": 1, "mode": "auto"})
                self.assertEqual(worker.select(None), second)
                self.assertEqual(worker.ready_jobs(), [
                    {"job_id": second, "book_key": "novel-b"}])
                other = root / "books" / "novel-a" / ".workflow" / "auto-control.json"
                worker.store_json(other, {"schema_version": 1, "mode": "auto"})
                self.assertEqual(worker.ready_jobs(), [
                    {"job_id": first, "book_key": "novel-a"},
                    {"job_id": second, "book_key": "novel-b"}])

    def test_blocked_primary_job_reserves_book_lane_against_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            slug = "one-book"
            earlier, duplicate = "8" + "0" * 31, "8" + "c" * 31
            for job in (earlier, duplicate):
                request = root / "intake" / "auto" / job / "request.json"
                request.parent.mkdir(parents=True)
                worker.store_json(request, {"job_id": job, "source_sha256": "a" * 64,
                                            "target_language": "ru"})
            for job, time_value in ((earlier, "2026-11-12T12:30:00+00:00"),
                                    (duplicate, "2026-11-12T12:00:00+00:00")):
                worker.store_json(root / "automation" / "status" / (job + ".json"), {
                    "job_id": job, "book_slug": slug, "state": "blocked",
                    "phase": "model-blocked", "active": None, "updated_at": time_value})
            control = root / "books" / slug / ".workflow" / "auto-control.json"
            worker.store_json(control, {"schema_version": 1, "mode": "auto",
                                        "updated_at": "2026-11-12T12:15:00+00:00"})
            with patch.object(worker, "ROOT", root):
                # The duplicate is resumable but primary is not: do not bypass primary.
                self.assertEqual(worker.ready_jobs(), [])
                worker.store_json(control, {"schema_version": 1, "mode": "auto",
                                            "updated_at": "2026-11-12T12:31:00+00:00"})
                self.assertEqual(worker.ready_jobs(), [
                    {"job_id": earlier, "book_key": slug}])

    def test_newer_resume_auto_signal_unblocks_only_supported_model_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            slug, job_id = "stalled-book", "a" * 32
            intake = root / "intake" / "auto" / job_id
            intake.mkdir(parents=True)
            worker.store_json(intake / "request.json", {
                "job_id": job_id, "source_sha256": "c" * 64,
                "target_language": "ru"})
            status_path = root / "automation" / "status" / (job_id + ".json")
            status = {"state": "blocked", "phase": "model-blocked",
                      "book_slug": slug, "active": None,
                      "updated_at": "2026-11-10T10:00:00+00:00"}
            worker.store_json(status_path, status)
            control = root / "books" / slug / ".workflow" / "auto-control.json"
            worker.store_json(control, {"schema_version": 1, "mode": "auto",
                "updated_at": "2026-11-10T10:01:00+00:00"})
            with patch.object(worker, "ROOT", root):
                self.assertTrue(worker.auto_resume_requested(status))
                self.assertEqual(worker.select(None), job_id)
                self.assertEqual(worker.ready_jobs(), [
                    {"job_id": job_id, "book_key": slug}])
                worker.store_json(control, {"schema_version": 1, "mode": "auto",
                    "updated_at": "2026-11-10T09:59:00+00:00"})
                self.assertFalse(worker.auto_resume_requested(status))
                self.assertEqual(worker.select(None), "")
                status["phase"] = "correction-limit-reached"
                worker.store_json(control, {"schema_version": 1, "mode": "auto",
                    "updated_at": "2026-11-10T10:01:00+00:00"})
                self.assertTrue(worker.auto_resume_requested(status))
                self.assertEqual(worker.select(None), job_id)
                self.assertEqual(worker.ready_jobs(), [
                    {"job_id": job_id, "book_key": slug}])
                status["phase"] = "operator-intervention-required"
                worker.store_json(control, {"schema_version": 1, "mode": "auto",
                    "updated_at": "2026-11-10T10:02:00+00:00"})
                self.assertFalse(worker.auto_resume_requested(status))

    def test_auto_pause_and_resume_do_not_change_literary_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            slug, job_id, revision = "handoff", "a" * 32, "b" * 40
            book = root / "books" / slug
            book.mkdir(parents=True)
            request = {"schema_version": 1, "job_id": job_id,
                       "target_language": "ru", "source_filename": "source.epub",
                       "source_sha256": "c" * 64, "workflow_revision": revision}
            meta = {"source": {"sha256": request["source_sha256"]},
                    "workflow": {"resolved_revision": revision}}
            worker.store_json(book / "metadata.json", meta)
            status = {"schema_version": 1, "job_id": job_id, "book_slug": slug,
                      "state": "running", "phase": "reviewer-completed",
                      "workflow_revision": revision}
            worker.store_json(root / "automation" / "status" / (job_id + ".json"), status)
            control = book / ".workflow" / "auto-control.json"
            worker.store_json(control, {"schema_version": 1, "mode": "chatops"})
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "request_for", return_value=request), \
                 patch.object(worker, "epub_identity", return_value=("Book", "Author")), \
                 patch.object(worker, "locate_book", return_value=(slug, meta)), \
                 patch.object(worker, "verify_legacy_reconciliation"), \
                 patch.object(worker, "install_engine", return_value=(root / "engine", revision)), \
                 patch.object(worker, "verify"), \
                 patch.object(worker, "checkpoint") as checkpoint, \
                 patch.object(worker, "cli") as cli:
                paused = worker.run(job_id)
                self.assertEqual((paused["state"], paused["phase"]),
                                 ("paused", "paused-for-chatops"))
                self.assertIsNone(paused["active"])
                cli.assert_not_called()
                checkpoint.assert_called_once()
                # Mimic the durable pause checkpoint, then ChatOps returns mode to Auto.
                worker.store_json(root / "automation" / "status" / (job_id + ".json"), paused)
                worker.store_json(control, {"schema_version": 1, "mode": "auto"})
                resumed = worker.run(job_id)
                self.assertEqual((resumed["state"], resumed["phase"]),
                                 ("running", "resumed-from-chatops"))
            self.assertEqual(worker.read_json(book / "metadata.json"), meta)

    def test_checkpoint_keeps_pinned_engine_and_stages_only_job_and_book(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'automation' / 'status').mkdir(parents=True)
            (root / 'scripts').mkdir()
            marker = root / '.book-translator-install.json'
            marker.write_text('pinned', encoding='utf-8')
            calls = []
            def fake_git(*args):
                calls.append(args)
                if args[0] == 'restore':
                    marker.write_text('default', encoding='utf-8')
                return ''
            with patch.object(worker, 'ROOT', root), patch.object(worker, 'git', side_effect=fake_git), \
                 patch.object(worker.subprocess, 'run') as process:
                process.return_value.returncode = 0
                worker.checkpoint('a' * 32, {'phase': 'matched'}, 'book')
            self.assertEqual(marker.read_text(encoding='utf-8'), 'pinned')
            self.assertIn(('add', '-A', '--', 'books/book', 'automation/status/' + 'a' * 32 + '.json'), calls)

    def test_checkpoint_accepts_git_terminal_nul_in_changed_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = "a" * 32
            slug = "test-book"
            (root / "books" / slug).mkdir(parents=True)
            (root / "books" / slug / "chapter.md").write_text(
                "translation", encoding="utf-8")
            def fake_git(*args):
                if args == ("diff", "--cached", "--name-only", "-z"):
                    return (f"books/{slug}/chapter.md{chr(0)}"
                            f"automation/status/{job_id}.json{chr(0)}")
                if args == ("rev-parse", "HEAD") or args == ("rev-parse", "origin/main"):
                    return "b" * 40
                return ""
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "git", side_effect=fake_git), \
                 patch.object(worker.subprocess, "run") as run:
                run.return_value.returncode = 0
                worker.checkpoint(job_id, {"phase": "claimed"}, slug)
            run.assert_called_once()
            self.assertIn("git", run.call_args.args[0])
            self.assertTrue((root / "automation" / "status" / (job_id + ".json")).exists())

    def test_disjoint_checkpoint_replays_without_touching_active_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def cmd(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
            cmd("init", "-q", "-b", "main")
            cmd("config", "user.name", "Test Agent")
            cmd("config", "user.email", "test-agent@example.test")
            (root / "books" / "our-book").mkdir(parents=True)
            (root / "books" / "our-book" / "chapter.md").write_text(
                "base", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "base")
            base = cmd("rev-parse", "HEAD")
            (root / "books" / "our-book" / "chapter.md").write_text(
                "our approved translation", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "our checkpoint")
            local = cmd("rev-parse", "HEAD")
            cmd("checkout", "-qb", "other-agent", base)
            (root / "books" / "another-book").mkdir(parents=True)
            (root / "books" / "another-book" / "chapter.md").write_text(
                "their unrelated translation", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "another book")
            remote = cmd("rev-parse", "HEAD")
            cmd("checkout", "-q", "main")
            # The active runner has dirty pinned-engine files after checkout.
            # Git plumbing must not reset or rebase this worktree.
            (root / "ephemeral-engine.py").write_text(
                "pinned version", encoding="utf-8")
            with patch.object(worker, "ROOT", root):
                merged = worker.replay_disjoint_checkpoint(
                    base, local, remote, ["books/our-book"])
            self.assertEqual(cmd("rev-parse", merged + "^"), remote)
            self.assertEqual(cmd("show", merged + ":books/our-book/chapter.md"),
                             "our approved translation")
            self.assertEqual(cmd("show", merged + ":books/another-book/chapter.md"),
                             "their unrelated translation")
            self.assertEqual(cmd("rev-parse", "HEAD"), local)
            self.assertEqual((root / "ephemeral-engine.py").read_text("utf-8"),
                             "pinned version")
            # A concurrent edit to the SAME book must never be replayed.
            cmd("checkout", "-qb", "conflicting-agent", base)
            (root / "books" / "our-book" / "chapter.md").write_text(
                "conflicting agent edit", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "conflicting work")
            conflicting = cmd("rev-parse", "HEAD")
            with patch.object(worker, "ROOT", root):
                with self.assertRaisesRegex(worker.JobError, "remote changed this job"):
                    worker.replay_disjoint_checkpoint(
                        base, local, conflicting, ["books/our-book"])

    def test_disjoint_checkpoint_classifies_stale_remote_as_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def cmd(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
            cmd("init", "-q", "-b", "main")
            cmd("config", "user.name", "Test Agent")
            cmd("config", "user.email", "test-agent@example.test")
            (root / "original.txt").write_text("first", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "previous remote")
            older_remote = cmd("rev-parse", "HEAD")
            (root / "other.txt").write_text("later", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "runner checkout base")
            base = cmd("rev-parse", "HEAD")
            (root / "books").mkdir()
            (root / "books" / "chapter.md").write_text("translated", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "local checkpoint")
            local = cmd("rev-parse", "HEAD")
            with patch.object(worker, "ROOT", root):
                with self.assertRaisesRegex(worker.JobError, "remote main is behind"):
                    worker.replay_disjoint_checkpoint(
                        base, local, older_remote, ["books/chapter.md"])
            self.assertEqual(cmd("rev-parse", "HEAD"), local)

    def test_checkpoint_refetches_behind_remote_without_forcing_or_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "books" / "sample").mkdir(parents=True)
            (root / "books" / "sample" / "chapter.md").write_text("accepted", encoding="utf-8")
            heads = iter(["b" * 40, "c" * 40])
            remotes = iter(["a" * 40, "d" * 40])
            calls = []
            def fake_git(*args):
                calls.append(args)
                if args == ("diff", "--cached", "--name-only", "-z"):
                    return "books/sample/chapter.md\0"
                if args == ("rev-parse", "HEAD"):
                    return next(heads)
                if args == ("rev-parse", "origin/main"):
                    return next(remotes)
                return ""
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "git", side_effect=fake_git), \
                 patch.object(worker, "replay_disjoint_checkpoint",
                              side_effect=[worker.JobError(
                                  "remote main is behind checkpoint base; retry fetch"), "e" * 40]) as replay, \
                 patch.object(worker.time, "sleep") as sleep, \
                 patch.object(worker.subprocess, "run") as run:
                run.return_value.returncode = 0
                worker.checkpoint("a" * 32, {"phase": "reviewer-completed"}, "sample")
            self.assertEqual(replay.call_count, 2)
            sleep.assert_called_once_with(1)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][:3], ["git", "push", "origin"])
            self.assertNotIn("--force", run.call_args.args[0])
            self.assertEqual(calls.count(("fetch", "--quiet", "origin", "main")), 2)

    def test_disjoint_checkpoint_rejects_rewritten_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def cmd(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
            cmd("init", "-q", "-b", "main")
            cmd("config", "user.name", "Test Agent")
            cmd("config", "user.email", "test-agent@example.test")
            (root / "base.txt").write_text("base", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "base")
            base = cmd("rev-parse", "HEAD")
            (root / "other.txt").write_text("local", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "local")
            local = cmd("rev-parse", "HEAD")
            # An unrelated root commit is not a safe fast-forward upstream.
            cmd("checkout", "--orphan", "rewritten")
            cmd("rm", "-q", "-rf", ".")
            (root / "replacement.txt").write_text("unrelated", encoding="utf-8")
            cmd("add", "-A")
            cmd("commit", "-qm", "replaced history")
            rewritten = cmd("rev-parse", "HEAD")
            with patch.object(worker, "ROOT", root):
                with self.assertRaisesRegex(worker.JobError, "upstream history diverged"):
                    worker.replay_disjoint_checkpoint(
                        base, local, rewritten, ["other.txt"])

    def test_user_approved_exact_artifacts_skip_repeat_review_but_new_unit_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "approved-book"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            source, translated = b"original text", "�������".encode()
            (book / "extracted" / "one.md").write_bytes(source)
            (book / "translated" / "one.md").write_bytes(translated)
            item = {"unit_id": "chapter-000001", "number": 1, "status": "translated",
                    "source_path": "extracted/one.md", "translation_path": "translated/one.md",
                    "translation_acceptance": {"source_sha256": hashlib.sha256(source).hexdigest(),
                                               "translation_sha256": hashlib.sha256(translated).hexdigest()}}
            worker.store_json(book / "progress.json", {"chapters": [item]})
            worker.store_json(book / "review-ledger.json", {"records": []})
            approvals = root / "automation" / "user-approvals" / "approved-book.json"
            approvals.parent.mkdir(parents=True)
            worker.store_json(approvals, {"schema_version": 1, "book_slug": "approved-book",
                "approved_by": "user", "approved_at": "2026-11-02T10:53:13Z",
                "workflow_revision": "a" * 40, "units": [{"unit_id": item["unit_id"],
                **item["translation_acceptance"]}]})
            with patch.object(worker, "ROOT", root):
                self.assertEqual(worker.next_operation("approved-book", {"correction_rounds": {}})[0], "finalize")
                new = {"unit_id": "chapter-000002", "number": 2, "status": "extracted"}
                worker.store_json(book / "progress.json", {"chapters": [item, new]})
                self.assertEqual(worker.next_operation("approved-book", {"correction_rounds": {}}), ("translator", new))
                (book / "translated" / "one.md").write_text("changed", encoding="utf-8")
                worker.store_json(book / "progress.json", {"chapters": [item]})
                self.assertEqual(worker.next_operation("approved-book", {"correction_rounds": {}})[0], "reviewer")
                (book / "translated" / "one.md").write_bytes(translated)
                (book / "extracted" / "one.md").write_bytes(b"changed source")
                self.assertEqual(worker.next_operation("approved-book", {"correction_rounds": {}})[0], "reviewer")

    def test_user_approved_output_remains_labelled_as_not_independently_reviewed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "approved-book"
            book.mkdir(parents=True)
            output = book / "output"
            output.mkdir()
            artifact = output / "approved-book.epub"
            artifact.write_bytes(b"PK\x03\x04approved")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            worker.store_json(output / "manifest.json", {"preview": True, "format": "epub",
                "artifact_path": "output/approved-book.epub", "artifact_sha256": digest})
            status = {"state": "running", "correction_rounds": {}}
            chapters = [{"unit_id": "chapter-000001", "status": "translated"}]
            with patch.object(worker, "ROOT", root), patch.object(worker, "verify"), \
                 patch.object(worker, "chapter_data", return_value=({}, {"records": []}, chapters)), \
                 patch.object(worker, "approved_unit_ids", return_value={"chapter-000001"}), \
                 patch.object(worker, "cli", side_effect=[json.dumps({"errors": [], "claims": [], "finalization": {"active": False}}),
                                                           json.dumps({"pending": []}), ""]) as cli, \
                 patch.object(worker, "checkpoint"):
                worker.finalize("a" * 32, "approved-book", status)
            self.assertEqual(cli.call_args.args, ("build", "approved-book", "--format", "epub", "--allow-unreviewed"))
            self.assertEqual(status["state"], "done")
            self.assertEqual(status["review_provenance"], "user-approved-unreviewed")

    def test_unapproved_translated_unit_cannot_use_user_approved_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            chapters = [{"unit_id": "chapter-000001", "status": "translated"},
                        {"unit_id": "chapter-000002", "status": "translated"}]
            with patch.object(worker, "ROOT", Path(folder)), patch.object(worker, "verify"), \
                 patch.object(worker, "chapter_data", return_value=({}, {"records": []}, chapters)), \
                 patch.object(worker, "approved_unit_ids", return_value={"chapter-000001"}), \
                 patch.object(worker, "cli") as cli:
                with self.assertRaisesRegex(worker.JobError, "lack current user approval"):
                    worker.finalize("a" * 32, "approved-book", {"state": "running"})
                cli.assert_not_called()

    def test_user_approval_never_overrides_recorded_reviewer_findings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "approved-book"
            book.mkdir(parents=True)
            chapter = {"unit_id": "chapter-000001", "number": 1, "status": "translated",
                       "translation_acceptance": {"translation_sha256": "a" * 64}}
            worker.store_json(book / "progress.json", {"chapters": [chapter]})
            worker.store_json(book / "review-ledger.json", {"records": [{"unit_id": chapter["unit_id"],
                "sequence": 1, "outcome": "CORRECTIONS_REQUIRED", "translation_sha256": "a" * 64,
                "quality_report": {"findings": [{"id": "meaning-1", "resolved": False}]}}]})
            with patch.object(worker, "ROOT", root), patch.object(worker, "approved_unit_ids", return_value={chapter["unit_id"]}):
                self.assertEqual(worker.next_operation("approved-book", {"correction_rounds": {}})[0], "corrector")
    def test_reconciled_history_guard_detects_changes_and_lost_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "stable-title"
            history = book / "source-updates" / "2026-10-11"
            history.mkdir(parents=True)
            (history / "translation.md").write_bytes(b"historical translation\n")
            rev = "a" * 40
            unit = {"unit_id": "chapter-000001", "status": "translated",
                    "translation_acceptance": {"translation_sha256": "b" * 64}}
            worker.store_json(book / "progress.json", {"chapters": [unit]})
            worker.store_json(book / "source-manifest.json", {
                "extracted": [{"unit_id": unit["unit_id"], "sha256": "c" * 64}]})
            snapshot = book / "source-revisions" / "source-000002" / "revision.json"
            worker.store_json(snapshot, {
                "revision_id": "source-000002", "metadata": {"source": {"sha256": "d" * 64}},
                "source_manifest": {"extracted": [{"unit_id": unit["unit_id"], "sha256": "c" * 64}]}})
            worker.store_json(book / "source-revisions.json", {"revisions": [{
                "revision_id": "source-000002", "source_sha256": "d" * 64,
                "snapshot_path": "source-revisions/source-000002/revision.json"}]})
            metadata = {"workflow": {"resolved_revision": rev}}
            worker.store_json(book / "automation-reconciliation.json", {
                "schema_version": 1, "workflow_revision": rev, "revision_id": "source-000002",
                "source_sha256": "d" * 64, "history_sha256": worker.history_digest(book / "source-updates"),
            })
            worker.verify_legacy_reconciliation(book, metadata)
            (history / "translation.md").write_bytes(b"changed history\n")
            with self.assertRaisesRegex(worker.BookIdentityError, "staged source updates"):
                worker.verify_legacy_reconciliation(book, metadata)
            (history / "translation.md").write_bytes(b"historical translation\n")
            unit["status"] = "extracted"
            worker.store_json(book / "progress.json", {"chapters": [unit]})
            with self.assertRaisesRegex(worker.BookIdentityError, "staged source updates"):
                worker.verify_legacy_reconciliation(book, metadata)
            worker.store_json(book / "source-manifest.json", {
                "extracted": [{"unit_id": unit["unit_id"], "sha256": "e" * 64}]})
            worker.verify_legacy_reconciliation(book, metadata)
    @staticmethod
    def make_epub(path: Path, title: str, author: str = "Author", chapter: str = "<p>Chapter</p>") -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="EPUB/package.opf"/></rootfiles></container>')
            archive.writestr("EPUB/package.opf", f'<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata></package>')
            archive.writestr("EPUB/chapter.xhtml", chapter)

    def test_book_identity_uses_title_not_filename_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "the-same-book"
            book.mkdir(parents=True)
            worker.store_json(book / "metadata.json", {
                "title": "The Same Book", "author": "Writer",
                "source_file": "edition.epub", "target_language": "ru",
            })
            with patch.object(worker, "ROOT", root):
                self.assertEqual(worker.locate_book("the same book", "Writer", "new.epub", "ru")[0], "the-same-book")
                self.assertEqual(worker.locate_book("Different title", "Writer", "edition.epub", "ru")[0], "different-title")

    def test_book_identity_blocks_ambiguous_titles_and_language_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for slug, language in (("same-a", "ru"), ("same-b", "ru")):
                book = root / "books" / slug
                book.mkdir(parents=True)
                worker.store_json(book / "metadata.json", {
                    "title": "Same Title", "author": "Writer", "target_language": language,
                })
            with patch.object(worker, "ROOT", root):
                with self.assertRaisesRegex(worker.BookIdentityError, "multiple distinct editions"):
                    worker.locate_book("Same Title", "Writer", "new.epub", "ru")
            shutil.rmtree(root / "books" / "same-b")
            with patch.object(worker, "ROOT", root):
                with self.assertRaisesRegex(worker.BookIdentityError, "different target language"):
                    worker.locate_book("Same Title", "Writer", "new.epub", "en")

    def test_duplicate_workspaces_for_same_edition_resolve_to_latest_book(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for slug, imported in (("auto-old", "2026-01-01T00:00:00Z"), ("auto-new", "2026-02-01T00:00:00Z")):
                book = root / "books" / slug
                book.mkdir(parents=True)
                worker.store_json(book / "metadata.json", {
                    "title": "Same Title", "author": "Writer", "target_language": "ru",
                    "imported_at": imported, "source": {"sha256": "a" * 64},
                    "workflow": {"resolved_revision": "b" * 40},
                })
            with patch.object(worker, "ROOT", root):
                self.assertEqual(worker.locate_book("Same Title", "Writer", "same.epub", "ru")[0], "auto-new")
                newer = root / "books" / "auto-new" / "metadata.json"
                value = worker.read_json(newer)
                value["source"]["sha256"] = "c" * 64
                worker.store_json(newer, value)
                with self.assertRaisesRegex(worker.BookIdentityError, "multiple distinct editions"):
                    worker.locate_book("Same Title", "Writer", "same.epub", "ru")

    def test_epub_identity_reads_package_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.epub"
            self.make_epub(path, "A New Book", "A Writer")
            self.assertEqual(worker.epub_identity(path), ("A New Book", "A Writer"))

    def test_same_edition_resumes_existing_book_without_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = "b" * 32
            source_dir = root / "intake" / "auto" / job
            source_dir.mkdir(parents=True)
            source = source_dir / "source.epub"
            self.make_epub(source, "Stable Title", "Writer")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            worker.store_json(source_dir / "request.json", {
                "schema_version": 1, "job_id": job, "source_sha256": digest,
                "target_language": "ru", "source_filename": "stable-title.epub",
            })
            book = root / "books" / "stable-title"
            book.mkdir(parents=True)
            worker.store_json(book / "metadata.json", {
                "title": "Stable Title", "author": "Writer", "target_language": "ru",
                "source": {"sha256": digest},
                "workflow": {"resolved_revision": "a" * 40},
            })
            engine = root / "engine"
            engine.mkdir()
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "install_engine", return_value=(engine, "a" * 40)), \
                 patch.object(worker, "verify"), \
                 patch.object(worker, "checkpoint"), \
                 patch.object(worker, "canonical_dispatch", return_value=("finalize", None)), \
                 patch.object(worker, "finalize", side_effect=lambda _job, _slug, status: status.update({"state": "done", "phase": "delivered"})) as finalize, \
                 patch.object(worker, "claim") as claim:
                status = worker.run(job)
            self.assertEqual(finalize.call_args.args[1], "stable-title")
            claim.assert_not_called()
            self.assertEqual(status["book_slug"], "stable-title")

    def test_new_edition_uses_canonical_source_update_in_same_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = "c" * 32
            source_dir = root / "intake" / "auto" / job
            source_dir.mkdir(parents=True)
            source = source_dir / "source.epub"
            self.make_epub(source, "Stable Title", "Writer", "<p>Changed chapter</p>")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            worker.store_json(source_dir / "request.json", {
                "schema_version": 1, "job_id": job, "source_sha256": digest,
                "target_language": "ru", "source_filename": "stable-title.epub",
            })
            book = root / "books" / "stable-title"
            book.mkdir(parents=True)
            worker.store_json(book / "metadata.json", {
                "title": "Stable Title", "author": "Writer", "target_language": "ru",
                "source": {"sha256": "0" * 64},
                "workflow": {"resolved_revision": "a" * 40},
            })
            engine = root / "engine"
            engine.mkdir()
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "install_engine", return_value=(engine, "a" * 40)), \
                 patch.object(worker, "verify"), \
                 patch.object(worker, "checkpoint"), \
                 patch.object(worker, "cli", return_value=json.dumps({"state": "promoted"})) as cli, \
                 patch.object(worker, "canonical_dispatch", return_value=("translator", {"number": 6})), \
                 patch.object(worker, "claim") as claim:
                status = worker.run(job)
            self.assertEqual(status["book_slug"], "stable-title")
            self.assertEqual(cli.call_args_list[0].args[:2], ("update-source", "stable-title"))
            self.assertEqual(cli.call_args_list[1].args,
                             ("source-coverage", "stable-title", "--source", str(source),
                              "--record", "--replace-stale", "--json"))
            self.assertEqual(claim.call_args.args[1], "stable-title")

    def test_staged_legacy_translation_blocks_before_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = "d" * 32
            intake = root / "intake" / "auto" / job
            intake.mkdir(parents=True)
            source = intake / "source.epub"
            self.make_epub(source, "Stable Title", "Writer")
            worker.store_json(intake / "request.json", {
                "schema_version": 1, "job_id": job,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "target_language": "ru", "source_filename": "book.epub",
            })
            book = root / "books" / "stable-title"
            (book / "source-updates" / "2026-10-11").mkdir(parents=True)
            (book / "source-updates" / "2026-10-11" / "README.md").write_text("staged translation")
            worker.store_json(book / "metadata.json", {
                "title": "Stable Title", "author": "Writer", "target_language": "ru",
                "workflow": {"resolved_revision": "a" * 40},
            })
            with patch.object(worker, "ROOT", root), patch.object(worker, "install_engine") as install:
                with self.assertRaisesRegex(worker.BookIdentityError, "staged source updates"):
                    worker.run(job)
                install.assert_not_called()

    def test_failed_coverage_blocks_claim_after_source_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = "e" * 32
            intake = root / "intake" / "auto" / job
            intake.mkdir(parents=True)
            source = intake / "source.epub"
            self.make_epub(source, "Stable Title", "Writer")
            worker.store_json(intake / "request.json", {
                "schema_version": 1, "job_id": job,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "target_language": "ru", "source_filename": "book.epub",
            })
            book = root / "books" / "stable-title"
            book.mkdir(parents=True)
            worker.store_json(book / "metadata.json", {
                "title": "Stable Title", "author": "Writer", "target_language": "ru",
                "source": {"sha256": "0" * 64},
                "workflow": {"resolved_revision": "a" * 40},
            })
            engine = root / "engine"
            engine.mkdir()
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "install_engine", return_value=(engine, "a" * 40)), \
                 patch.object(worker, "verify"), patch.object(worker, "checkpoint"), \
                 patch.object(worker, "cli", side_effect=[json.dumps({"state": "promoted"}), worker.JobError("coverage failed")]), \
                 patch.object(worker, "claim") as claim:
                with self.assertRaisesRegex(worker.JobError, "coverage failed"):
                    worker.run(job)
                claim.assert_not_called()

    def test_strict_review_blocks_unresolved_findings(self):
        payload = {
            "outcome": "PASS",
            "checks": dict.fromkeys(("coverage", "meaning", "terminology", "style", "structure"), True),
            "findings": [{"severity": "major", "detail": "Omission", "resolved": False}],
        }
        with self.assertRaises(ModelError):
            parse_review(json.dumps(payload))
        payload["outcome"] = "CORRECTIONS_REQUIRED"
        outcome, report = parse_review(json.dumps(payload))
        self.assertEqual(outcome, "CORRECTIONS_REQUIRED")
        self.assertEqual(report["context"], "independent_session")

    def test_reviewer_must_complete_all_checks(self):
        payload = {
            "outcome": "PASS",
            "checks": dict.fromkeys(("coverage", "meaning", "terminology", "style", "structure"), True),
            "findings": [],
        }
        payload["checks"]["coverage"] = False
        with self.assertRaises(ModelError):
            parse_review(json.dumps(payload))
        payload["checks"]["coverage"] = True
        self.assertEqual(parse_review(json.dumps(payload))[0], "PASS")

    def test_model_endpoint_requires_https_and_configured_role(self):
        with patch.dict(os.environ, {
            "OMNIROUTE_BASE_URL": "http://example.com/v1",
            "OMNIROUTE_API_KEY": "test-placeholder",
            "OMNIROUTE_TRANSLATOR_MODEL": "test-model",
        }):
            with self.assertRaises(ModelError):
                config("TRANSLATOR")
        with self.assertRaises(ModelError):
            config("UNKNOWN")

    def test_model_requests_json_object_mode(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit):
                return json.dumps({"choices": [{"message": {"content": '{"translation":"ok"}'},
                                                 "finish_reason": "stop"}]}).encode()

        with patch.dict(os.environ, {
            "OMNIROUTE_BASE_URL": "https://api.example.com/v1",
            "OMNIROUTE_API_KEY": "test-placeholder",
            "OMNIROUTE_TRANSLATOR_MODEL": "test-model",
        }), patch("omniroute.urlopen", return_value=Response()) as open_url:
            answer = complete("TRANSLATOR", "system", "user")
        request = open_url.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(answer, '{"translation":"ok"}\n')

    def test_context_scopes_model_to_one_supplied_unit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "books" / "example" / "extracted").mkdir(parents=True)
            source = root / "books" / "example" / "extracted" / "001.md"
            source.write_text("[*Metadata.example*](https://metadata.example)\n", encoding="utf-8")
            chapter = {"unit_id": "chapter-000002", "number": 2, "source_path": "extracted/001.md",
                       "translation_path": "translated/001.md", "status": "extracted"}
            chapters = [dict(chapter, unit_id=f"chapter-{n:06d}", number=n,
                             source_path=f"extracted/{n:03d}.md",
                             translation_path=f"translated/{n:03d}.md")
                        for n in range(1, 52)]
            chapters[1] = chapter
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "role_contracts", return_value=("translator contract", [])), \
                 patch.object(worker, "full_shared_context", return_value=("", "")), \
                 patch.object(worker, "chapter_data", return_value=({}, {}, chapters)), \
                 patch.object(worker, "read_json", return_value={
                     "title": "Example", "target_language": "ru", "chapter_count": 51,
                     "source_sha256": "a" * 64,
                 }):
                _system, user, _source = worker.context(Path("engine"), "example", chapter, "translator")
            self.assertIn("source unit 2 of 51", user)
            self.assertIn('"target_language": "ru"', user)
            self.assertNotIn("chapter_count", user)
            self.assertNotIn("source_sha256", user)
            self.assertIn("0 protected EPUB marker(s) and 1 Markdown link(s)", user)
            self.assertIn("ZXBTLINK", user)
            self.assertNotIn("https://metadata.example", user)
            self.assertIn("Preserve a unit consisting only of external links exactly", _system)

    def test_reviewer_can_audit_nonprose_source_units(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "example"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            source = "A Home in You Playlist\n[Song](https://example.com/song)\n"
            (book / "extracted" / "001.md").write_text(source, encoding="utf-8")
            (book / "translated" / "001.md").write_text(source, encoding="utf-8")
            chapter = {"unit_id": "chapter-000001", "number": 1,
                       "source_path": "extracted/001.md",
                       "translation_path": "translated/001.md", "status": "translated"}
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "role_contracts", return_value=("reviewer contract", [])), \
                 patch.object(worker, "full_shared_context", return_value=("", "")), \
                 patch.object(worker, "chapter_data", return_value=({}, {"records": []}, [chapter])), \
                 patch.object(worker, "read_json", return_value={"title": "Example", "target_language": "ru"}):
                _system, user, _source = worker.context(Path("engine"), "example", chapter, "reviewer")
            self.assertIn("metadata rather than prose", user)
            self.assertIn("not a failure after confirming it is not applicable", user)

    def test_link_boundaries_restore_original_destinations_and_translated_labels(self):
        source = "Before [Read more](https://example.com/book) after.\n"
        masked, links = worker.mask_markdown_links(source)
        self.assertEqual(len(links), 1)
        self.assertNotIn("https://example.com/book", masked)
        translated = masked.replace("Read more", "���������")
        restored = worker.restore_markdown_links(source, translated)
        self.assertEqual(restored, "Before [���������](https://example.com/book) after.\n")
        with self.assertRaisesRegex(worker.JobError, "link boundary sequence differs"):
            worker.restore_markdown_links(source, "Before ��������� after.\n")

    def test_external_link_only_source_is_deterministically_preserved(self):
        link = "[*Metadata.example*](https://metadata.example)\n"
        self.assertTrue(worker.external_link_only(link))
        self.assertFalse(worker.external_link_only(link + "A sentence of prose.\n"))
        self.assertFalse(worker.external_link_only("![cover](https://example.com/cover.jpg)\n"))

    def test_external_links_and_labels_are_protected(self):
        source = "[*Metadata.example*](https://metadata.example)\n"
        worker.check_markers(source, source)
        worker.check_markers(source, "[Metadata.example](https://metadata.example)\n")
        with self.assertRaisesRegex(worker.JobError, "link destination differs"):
            worker.check_markers(source, "[*Metadata.example*](https://example.com)\n")
        with self.assertRaisesRegex(worker.JobError, "link destination differs"):
            worker.check_markers(source, "The link was omitted.\n")
        block_source = "<!-- bt-block:000001 -->\n" + source
        with self.assertRaisesRegex(worker.JobError, "external hyperlink label"):
            worker.check_markers(block_source,
                                 "<!-- bt-block:000001 -->\n[Metadata.example](https://metadata.example)\n")

    def test_blocked_resume_is_audited_and_preserves_call_count(self):
        status = {"state": "blocked", "phase": "model-blocked", "active": None,
                  "failures": 3, "model_calls_started": 7,
                  "last_error": "marker mismatch"}
        worker.approve_blocked_resume(status)
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["failures"], 0)
        self.assertEqual(status["model_calls_started"], 7)
        self.assertEqual(status["recoveries"][0]["previous_error"], "marker mismatch")
        with self.assertRaisesRegex(worker.JobError, "inactive blocked job with a supported recovery path"):
            worker.approve_blocked_resume({"state": "blocked", "phase": "operator-intervention-required",
                                          "active": None})

    def test_interrupted_resume_reuses_only_the_matching_live_claim(self):
        status = {"state": "blocked", "phase": "operator-intervention-required",
                  "active": {"role": "corrector", "chapter": 6, "session_id": "session-1"},
                  "failures": 1, "model_calls_started": 26,
                  "last_error": "glossary conflict"}
        unit = {"unit_id": "chapter-000006"}
        claim = {"unit_id": "chapter-000006", "role": "translator", "session_id": "session-1"}
        worker.approve_interrupted_resume(status, unit, claim, [claim])
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["active"]["session_id"], "session-1")
        self.assertEqual(status["model_calls_started"], 26)
        self.assertEqual(status["recoveries"][0]["action"], "resume-interrupted-claim")
        unchanged = {"state": "blocked", "phase": "operator-intervention-required",
                     "active": {"role": "corrector", "chapter": 6, "session_id": "session-1"}}
        with self.assertRaisesRegex(worker.JobError, "canonical active claim"):
            worker.approve_interrupted_resume(unchanged, unit, claim, [])
        self.assertEqual(unchanged["state"], "blocked")

    def test_run_resumes_live_interrupted_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = "f" * 32
            intake = root / "intake" / "auto" / job
            intake.mkdir(parents=True)
            source = intake / "source.epub"
            self.make_epub(source, "Stable Title", "Writer")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            worker.store_json(intake / "request.json", {
                "schema_version": 1, "job_id": job, "source_sha256": digest,
                "target_language": "ru", "source_filename": "stable-title.epub",
            })
            book = root / "books" / "stable-title"
            book.mkdir(parents=True)
            revision = "a" * 40
            worker.store_json(book / "metadata.json", {
                "title": "Stable Title", "author": "Writer", "target_language": "ru",
                "source": {"sha256": digest}, "workflow": {"resolved_revision": revision},
            })
            chapter = {"unit_id": "chapter-000006", "number": 6}
            claim = {"unit_id": chapter["unit_id"], "role": "translator",
                     "session_id": "session-1", "expires_at": "2099-01-01T00:00:00+00:00"}
            worker.store_json(book / ".workflow" / "claims" / (chapter["unit_id"] + ".json"), claim)
            worker.store_json(root / "automation" / "status" / (job + ".json"), {
                "schema_version": 1, "job_id": job, "book_slug": "stable-title",
                "source_sha256": digest, "workflow_revision": revision,
                "state": "blocked", "phase": "operator-intervention-required",
                "active": {"role": "corrector", "chapter": 6, "session_id": "session-1"},
                "model_calls_started": 26, "failures": 1,
            })
            engine = root / "engine"
            engine.mkdir()
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "install_engine", return_value=(engine, revision)), \
                 patch.object(worker, "verify"), \
                 patch.object(worker, "chapter_data", return_value=({}, {}, [chapter])), \
                 patch.object(worker, "cli", return_value=json.dumps({"claims": [claim], "errors": []})), \
                 patch.object(worker, "checkpoint"), \
                 patch.object(worker, "process_claim") as process:
                status = worker.run(job, resume_interrupted=True)
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["model_calls_started"], 26)
            self.assertEqual(status["active"]["session_id"], "session-1")
            process.assert_called_once_with(job, "stable-title", status, engine)

    def test_conflicting_glossary_proposal_is_rejected_without_changing_canonical_glossary(self):
        with tempfile.TemporaryDirectory() as folder:
            book = Path(folder)
            current = ("# Glossary\n\n| Original | Translation | Type | Notes |\n"
                       "| --- | --- | --- | --- |\n| Dillon | Dillon | character | Canonical |\n")
            (book / "glossary.md").write_text(current, encoding="utf-8")
            accepted, rejected = worker.filter_conflicting_glossary_proposals(book, [
                {"original": "Dillon", "translation": "������", "type": "character", "notes": "Conflict"},
                {"original": "Mara", "translation": "����", "type": "character", "notes": "New"},
            ])
            self.assertEqual(rejected, 1)
            self.assertEqual([row["original"] for row in accepted], ["Mara"])
            self.assertEqual((book / "glossary.md").read_text("utf-8"), current)

    def test_resume_preflight_failure_does_not_rewrite_blocked_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = "a" * 32
            worker.store_json(root / "automation" / "status" / (job_id + ".json"), {
                "schema_version": 1, "job_id": job_id, "state": "blocked",
                "phase": "model-blocked", "active": None, "failures": 3,
            })
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "run", side_effect=worker.JobError("preflight stopped")), \
                 patch.object(worker, "block_on_unexpected_failure") as quarantine, \
                 patch.object(sys, "argv", ["book_runner.py", "--job-id", job_id,
                                               "--resume-blocked"]):
                self.assertEqual(worker.main(), 1)
            quarantine.assert_not_called()
            stored = worker.read_json(root / "automation" / "status" / (job_id + ".json"))
            self.assertEqual(stored["state"], "blocked")
            self.assertEqual(stored["phase"], "model-blocked")

    def test_markers_are_order_and_identity_bound(self):
        source = "<!-- bt-block:000001 -->\nFirst [link](#x)\n<!-- bt-block:000002 -->\n"
        worker.check_markers(source, "<!-- bt-block:000001 -->\n������ [������](#x)\n<!-- bt-block:000002 -->\n")
        with self.assertRaises(worker.JobError):
            worker.check_markers(source, "<!-- bt-block:000002 -->\n������ [������](#x)\n<!-- bt-block:000001 -->\n")
        with self.assertRaises(worker.JobError):
            worker.check_markers(source, "<!-- bt-block:000001 -->\n������ [������](#y)\n<!-- bt-block:000002 -->\n")

    def test_scheduler_skips_blocked_and_unreadable_statuses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ids = ["0" * 32, "1" * 32, "2" * 32, "3" * 32]
            for job_id in ids:
                request = root / "intake" / "auto" / job_id / "request.json"
                request.parent.mkdir(parents=True)
                request.write_text("{}", encoding="utf-8")
            worker.store_json(root / "automation" / "status" / (ids[0] + ".json"),
                              {"state": "blocked"})
            bad = root / "automation" / "status" / (ids[1] + ".json")
            bad.write_text("{invalid", encoding="utf-8")
            worker.store_json(root / "automation" / "status" / (ids[2] + ".json"),
                              {"state": "done"})
            with patch.object(worker, "ROOT", root):
                self.assertEqual(worker.select(None), ids[3])
                # Explicit attempts remain operator-controlled and are never
                # silently redirected to another request.
                self.assertEqual(worker.select(ids[0]), ids[0])
            worker.store_json(root / "automation" / "status" / (ids[3] + ".json"),
                              {"state": "blocked"})
            with patch.object(worker, "ROOT", root):
                self.assertEqual(worker.select(None), "")

    def test_quarantine_failure_preserves_claim_without_staging_book(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = "f" * 32
            slug = "existing-book"
            book = root / "books" / slug
            book.mkdir(parents=True)
            (book / "translated.md").write_text("uncommitted mutation", encoding="utf-8")
            worker.store_json(root / "automation" / "status" / (job_id + ".json"),
                              {"job_id": job_id, "book_slug": slug, "state": "running",
                               "phase": "claimed", "model_calls_started": 3,
                               "active": {"role": "reviewer", "session_id": "owner"}})
            calls = []
            def fake_git(*args):
                calls.append(args)
                if args[:2] in (("rev-parse", "HEAD"), ("rev-parse", "origin/main")):
                    return "a" * 40
                if args[:2] == ("diff", "--cached"):
                    # No staged changes prior to status quarantine; the newly
                    # staged status is the only staged path after git add.
                    if sum(call[:2] == ("diff", "--cached") for call in calls) == 1:
                        return ""
                    return "automation/status/" + job_id + ".json"
                return ""
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "git", side_effect=fake_git), \
                 patch.object(worker.subprocess, "run") as process:
                process.return_value.returncode = 0
                status = worker.block_on_unexpected_failure(
                    job_id, worker.JobError("source coverage blocked"))
            self.assertEqual(status["state"], "blocked")
            self.assertEqual(status["phase"], "operator-intervention-required")
            self.assertEqual(status["model_calls_started"], 3)
            self.assertEqual(status["active"]["session_id"], "owner")
            self.assertIn("claim", status["recovery_note"])
            self.assertEqual(worker.read_json(root / "automation" / "status" / (job_id + ".json"))["state"],
                             "blocked")
            self.assertIn(("add", "-A", "--", "automation/status/" + job_id + ".json"), calls)
            self.assertFalse(any(any("books/" in part for part in call) for call in calls))
            self.assertEqual((book / "translated.md").read_text("utf-8"), "uncommitted mutation")

    def test_status_quarantine_refuses_existing_staged_literary_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = "c" * 32
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "git", return_value="books/other/chapter.md") as git:
                with self.assertRaisesRegex(worker.JobError, "staged changes"):
                    worker.block_on_unexpected_failure(job_id, worker.JobError("bad CLI"))
            self.assertFalse((root / "automation" / "status" / (job_id + ".json")).exists())
            self.assertEqual(git.call_args_list[0].args, ("diff", "--cached", "--name-only"))

    def test_main_records_preflight_failure_and_advances_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = "e" * 32
            outputs = root / "github_outputs"
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "run", side_effect=worker.JobError(
                     "EPUB source coverage blocked: unsupported semantic element <ol>")), \
                 patch.object(worker, "checkpoint") as checkpoint, \
                 patch.object(sys, "argv", ["book_runner.py", "--job-id", job_id]), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": str(outputs)}):
                self.assertEqual(worker.main(), 0)
            stored = checkpoint.call_args.args[1]
            self.assertEqual(stored["state"], "blocked")
            self.assertIn("unsupported semantic element <ol>", stored["last_error"])
            self.assertEqual(checkpoint.call_args.args[2], None)
            self.assertIn("continue_queue=true", outputs.read_text("utf-8"))
            self.assertNotIn("continue=true", outputs.read_text("utf-8"))

    def test_quarantine_failure_is_not_claimed_success_if_checkpoint_fails(self):
        with patch.object(worker, "run", side_effect=worker.JobError("failed")), \
             patch.object(worker, "block_on_unexpected_failure",
                          side_effect=worker.JobError("stale git head")), \
             patch.object(sys, "argv", ["book_runner.py", "--job-id", "d" * 32]):
            self.assertEqual(worker.main(), 1)

    def test_intake_hash_bound_and_source_filename_fixed(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker, "ROOT", Path(folder)):
                job_id = "a" * 32
                source_dir = worker.job_path(job_id)
                source_dir.mkdir(parents=True)
                source = b"PK\x03\x04test epub bytes"
                (source_dir / "source.epub").write_bytes(source)
                request = {"schema_version": 1, "job_id": job_id,
                           "target_language": "ru",
                           "source_sha256": hashlib.sha256(source).hexdigest()}
                worker.store_json(source_dir / "request.json", request)
                self.assertEqual(worker.request_for(job_id)["job_id"], job_id)
                self.assertEqual(worker.select(None), job_id)
                request["source_sha256"] = "0" * 64
                worker.store_json(source_dir / "request.json", request)
                with self.assertRaises(worker.JobError):
                    worker.request_for(job_id)

    def test_unexpected_intake_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker, "ROOT", Path(folder)):
                job_id = "a" * 32
                source_dir = worker.job_path(job_id)
                source_dir.mkdir(parents=True)
                source = b"PK\x03\x04test"
                (source_dir / "source.epub").write_bytes(source)
                request = {"schema_version": 1, "job_id": job_id,
                           "target_language": "ru",
                           "source_sha256": hashlib.sha256(source).hexdigest(),
                           "unexpected": True}
                worker.store_json(source_dir / "request.json", request)
                with self.assertRaisesRegex(worker.JobError, "invalid"):
                    worker.request_for(job_id)

    def test_chatops_missing_findings_rechecks_without_blind_correction(self):
        chapter = {
            "unit_id": "chapter-000022", "number": 22, "status": "translated",
            "translation_acceptance": {
                "source_sha256": "b" * 64,
                "translation_sha256": "a" * 64,
            },
        }
        records = [
            {
                "unit_id": chapter["unit_id"], "sequence": index,
                "outcome": "CORRECTIONS_REQUIRED",
                "source_sha256": "b" * 64, "translation_sha256": "a" * 64,
                **({"quality_report": {"findings": [
                    {"id": "meaning-1", "resolved": False}]}} if index == 1 else {}),
            }
            for index in (1, 2, 3)
        ]
        ledger = {"records": records}
        decision = json.dumps({
            "operation": "correct_translation",
            "unit_id": chapter["unit_id"],
            "chapter_number": 22,
        })
        with patch.object(worker, "chapter_data", return_value=({}, ledger, [chapter])), \
             patch.object(worker, "approved_unit_ids", return_value=set()), \
             patch.object(worker, "cli", return_value=decision):
            self.assertEqual(
                worker.canonical_dispatch("synthetic-volume", {"correction_rounds": {}}),
                ("reviewer", chapter),
            )
            records[-1]["quality_report"] = {"findings": [
                {"id": "meaning-2", "resolved": False}]}
            self.assertEqual(
                worker.canonical_dispatch("synthetic-volume", {"correction_rounds": {}}),
                ("corrector", chapter),
            )

            records.append({
                "unit_id": chapter["unit_id"], "sequence": 4,
                "outcome": "CORRECTIONS_REQUIRED",
                "source_sha256": "b" * 64, "translation_sha256": "a" * 64,
            })
            with patch.object(worker, "next_operation", return_value=(
                "review-deferred", None)) as following:
                status = {"correction_rounds": {}}
                self.assertEqual(
                    worker.canonical_dispatch("synthetic-volume", status),
                    ("review-deferred", None),
                )
                self.assertIn(chapter["unit_id"], status["deferred_review_units"])
                following.assert_called_once()

    def test_chatops_missing_findings_in_user_approved_path(self):
        chapter = {
            "unit_id": "chapter-000022", "number": 22, "status": "translated",
            "translation_acceptance": {
                "source_sha256": "b" * 64, "translation_sha256": "a" * 64,
            },
        }
        ledger = {"records": [{
            "unit_id": chapter["unit_id"], "sequence": 1,
            "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": "b" * 64, "translation_sha256": "a" * 64,
        }]}
        operations = ModuleType("workflow.operations")
        operations.next_unit_operation = lambda state, review: (
            "correct_translation" if review == "corrections_required" else "review"
        )
        workflow = ModuleType("workflow")
        workflow.__path__ = []
        with patch.dict(sys.modules, {
            "workflow": workflow, "workflow.operations": operations,
        }), patch.object(worker, "chapter_data", return_value=(
            {}, ledger, [chapter],
        )), patch.object(worker, "approved_unit_ids", return_value={
            chapter["unit_id"],
        }):
            self.assertEqual(
                worker.next_operation("synthetic-volume", {"correction_rounds": {}}),
                ("reviewer", chapter),
            )

    def test_automatic_subprocess_loads_pinned_classifier_after_review_defer(self):
        # A fresh GitHub Actions job starts from automation/, not scripts/.
        # Exercise the same import boundary in a separate Python interpreter
        # rather than relying on workflow modules injected by other tests.
        repo = Path(__file__).resolve().parents[1]
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "automation"))
import book_runner
chapter = {"unit_id": "chapter-000002", "number": 2, "status": "extracted"}
with patch.object(book_runner, "chapter_data",
                  return_value=({}, {"records": []}, [chapter])), \\
     patch.object(book_runner, "approved_unit_ids", return_value=set()):
    operation, selected = book_runner.next_operation("synthetic", {"correction_rounds": {}})
    assert operation == "translator" and selected == chapter, (operation, selected)
"""
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-c", script, str(repo)], cwd=temp,
                capture_output=True, text=True, env=env, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_next_step_requires_independent_review(self):
        with tempfile.TemporaryDirectory() as folder:
            workflow = ModuleType("workflow")
            workflow.__path__ = []
            operations = ModuleType("workflow.operations")
            operations.next_unit_operation = lambda lifecycle, review: (
                "translate" if lifecycle == "extracted" else
                {"pass": "accept_review", "corrections_required": "correct_translation",
                 "missing": "review", "stale": "review"}.get(review)
            )
            with patch.dict(sys.modules, {"workflow": workflow,
                                           "workflow.operations": operations}), \
                 patch.object(worker, "ROOT", Path(folder)):
                slug = "auto-" + "a" * 32
                book = worker.ROOT / "books" / slug
                book.mkdir(parents=True)
                chapter = {
                    "unit_id": "chapter-000001", "number": 1, "status": "translated",
                    "translation_acceptance": {"translation_sha256": "a" * 64}
                }
                worker.store_json(book / "progress.json", {"chapters": [chapter]})
                worker.store_json(book / "review-ledger.json", {"records": []})
                status = {"correction_rounds": {}}
                self.assertEqual(worker.next_operation(slug, status)[0], "reviewer")
                worker.store_json(book / "review-ledger.json", {"records": [{
                    "unit_id": chapter["unit_id"], "sequence": 1, "outcome": "CORRECTIONS_REQUIRED",
                    "translation_sha256": "a" * 64,
                    "quality_report": {"findings": [{"id": "meaning-1", "resolved": False}]}}]})
                self.assertEqual(worker.next_operation(slug, status)[0], "corrector")
                status["correction_rounds"]["chapter-000001"] = worker.MAX_CORRECTIONS
                self.assertEqual(worker.next_operation(slug, status)[0], "blocked")

    def test_review_bound_protocol_retry_is_one_shot_and_hash_checked(self):
        unit = "chapter-000026"
        review_id = "a" * 32
        sha = "b" * 64
        chapter = {"unit_id": unit, "number": 26, "status": "translated",
                   "translation_acceptance": {"translation_sha256": sha}}
        record = {"unit_id": unit, "sequence": 1, "record_id": review_id,
                  "outcome": "CORRECTIONS_REQUIRED", "translation_sha256": sha}
        ledger = {"records": [record]}
        status = {"phase": "model-blocked", "failures": worker.MAX_FAILURES,
                  "blocked_unit_id": unit,
                  "last_error": "patch must target the reviewer's exact translation anchor",
                  "last_patch_anchor_mismatch": {
                      "unit_id": unit, "review_record_id": review_id,
                      "translation_sha256": sha}}
        self.assertTrue(worker.allow_review_bound_protocol_retry(status, ledger, [chapter]))
        self.assertFalse(worker.allow_review_bound_protocol_retry(status, ledger, [chapter]))
        self.assertEqual(len(status["correction_protocol_retry_audit"]), 1)
        self.assertEqual(status["correction_protocol_retry_audit"][0]["protocol"],
                         "review-bound-patches-v1")
        self.assertNotIn("deferred_review_units", status)
        self.assertEqual(status["failures"], worker.MAX_FAILURES)
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(record["outcome"], "CORRECTIONS_REQUIRED")
        self.assertTrue(worker.defer_exhausted_patch_mismatch(status, ledger, [chapter]))
        self.assertEqual(status["deferred_review_units"], [unit])

        status["correction_protocol_retry_audit"] = []
        chapter["translation_acceptance"]["translation_sha256"] = "c" * 64
        with self.assertRaisesRegex(worker.JobError, "unchanged review"):
            worker.allow_review_bound_protocol_retry(status, ledger, [chapter])
        status["last_error"] = "unrelated model error"
        self.assertFalse(worker.allow_review_bound_protocol_retry(status, ledger, [chapter]))

    def test_exhausted_patch_mismatch_defers_only_unchanged_unit(self):
        chapter = {"unit_id": "chapter-000024", "number": 24, "status": "translated",
                   "translation_acceptance": {"translation_sha256": "a" * 64}}
        record = {"unit_id": chapter["unit_id"], "record_id": "review-24",
                  "sequence": 2, "outcome": "CORRECTIONS_REQUIRED",
                  "translation_sha256": "a" * 64}
        status = {"phase": "model-blocked", "failures": 3,
                  "blocked_unit_id": chapter["unit_id"],
                  "last_error": "patch must target the reviewer's exact translation anchor",
                  "last_patch_anchor_mismatch": {
                      "unit_id": chapter["unit_id"], "review_record_id": "review-24",
                      "translation_sha256": "a" * 64}}
        ledger = {"records": [record]}
        self.assertTrue(worker.defer_exhausted_patch_mismatch(status, ledger, [chapter]))
        self.assertEqual(status["deferred_review_units"], [chapter["unit_id"]])
        self.assertEqual(status["patch_deferral_audit"][0]["review_record_id"], "review-24")
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(record["outcome"], "CORRECTIONS_REQUIRED")
        status["last_patch_anchor_mismatch"]["translation_sha256"] = "b" * 64
        with self.assertRaisesRegex(worker.JobError, "unchanged review"):
            worker.defer_exhausted_patch_mismatch(status, ledger, [chapter])

    def test_reviewer_anchor_is_bound_from_id_and_unsafe_legacy_target_rejected(self):
        from literary_context import PatchAnchorMismatch
        finding = {"id": "meaning-1", "resolved": False,
                   "translation_anchor": {"quote": "Неверная фраза.", "occurrence": 0}}
        response = json.dumps({
            "patches": [{"finding_id": "meaning-1", "operation": "replace",
                         "replacement": "Исправленный текст."}],
            "glossary": [], "style_guide": [],
        }, ensure_ascii=False)
        patches, glossary, style = worker.parse_correction_patches(response, [finding])
        self.assertEqual(patches[0]["target_anchor"], finding["translation_anchor"])
        self.assertEqual((glossary, style), ([], []))
        self.assertEqual(
            worker.apply_correction_patches("Начало. Неверная фраза. Конец.", patches),
            "Начало. Исправленный текст. Конец."
        )
        legacy = json.dumps({
            "patches": [{"finding_id": "meaning-1", "operation": "replace",
                         "target_anchor": {"quote": "Неверная фраза. Конец.", "occurrence": 0},
                         "replacement": "PRIVATE_REPLACEMENT_NOT_LOGGED"}],
            "glossary": [], "style_guide": [],
        }, ensure_ascii=False)
        with self.assertRaises(PatchAnchorMismatch) as captured:
            worker.parse_correction_patches(legacy, [finding])
        self.assertEqual(captured.exception.expected_anchor, finding["translation_anchor"])
        self.assertNotIn("PRIVATE_REPLACEMENT_NOT_LOGGED", str(captured.exception))

    def test_invalid_omission_anchor_fails_closed_without_leaking_model_content(self):
        slug, job_id, session = "private-diagnostic-test", "a" * 32, "test-corrector"
        unit_id = "chapter-000001"
        chapter = {"unit_id": unit_id, "number": 1, "status": "translated",
                   "translation_acceptance": {"translation_sha256": "a" * 64}}
        finding = {"id": "omission-1", "resolved": False,
                   "translation_anchor": None}
        ledger = {"records": [{
            "unit_id": unit_id, "sequence": 1, "record_id": "review-1",
            "outcome": "CORRECTIONS_REQUIRED", "quality_report": {
                "evidence_version": 2, "findings": [finding]}}]}
        response = json.dumps({
            "patches": [{"finding_id": "omission-1", "operation": "insert_after",
                         "insertion_anchor": {"quote": "", "occurrence": 0},
                         "replacement": "PRIVATE_REPLACEMENT_NOT_LOGGED"}],
            "glossary": [], "style_guide": [],
        }, ensure_ascii=False)
        status = {"state": "running", "phase": "claimed", "failures": 0,
                  "model_calls_started": 1, "workflow_revision": "b" * 40,
                  "active": {"role": "corrector", "chapter": 1, "session_id": session}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books" / slug
            (book / ".workflow" / "claims").mkdir(parents=True)
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            worker.store_json(book / ".workflow" / "claims" / (unit_id + ".json"),
                              {"session_id": session})
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "chapter_data", return_value=({}, ledger, [chapter])), \
                 patch.object(worker, "context", return_value=("system", "prompt", "source")), \
                 patch.object(worker, "role_contracts", return_value=("contract", ["AGENTS.md"])), \
                 patch.object(worker, "complete", return_value=response), \
                 patch.object(worker, "process_correction_batches",
                              side_effect=ModelError("corrector insertion anchor is invalid")), \
                 patch.object(worker, "load_public_correction_helper",
                              return_value=type("TypedProtocolStub", (), {
                                  "failure_code": staticmethod(lambda error: "invalid_anchor"),
                              })), \
                 patch.object(worker, "cli") as cli, \
                 patch.object(worker, "checkpoint") as checkpoint:
                worker.process_claim(job_id, slug, status, root)
                self.assertEqual(status["phase"], "model-retry")
                self.assertIsNone(status["active"])
                self.assertNotIn("last_patch_anchor_mismatch", status)
                self.assertEqual(status["last_error"],
                                 "corrector insertion anchor is invalid")
                self.assertNotIn("PRIVATE_REPLACEMENT_NOT_LOGGED",
                                 json.dumps(status, ensure_ascii=False))
                cli.assert_called_once_with("release", slug, "1", "--session-id", session)
                checkpoint.assert_called()

    def test_legacy_correction_budget_defers_unmodified_reviewed_unit(self):
        chapter = {"unit_id": "chapter-000025", "number": 25, "status": "translated",
                   "translation_acceptance": {"source_sha256": "a" * 64,
                                              "translation_sha256": "b" * 64}}
        record = {"unit_id": chapter["unit_id"], "sequence": 1,
                  "record_id": "c" * 32,
                  "source_sha256": "a" * 64, "translation_sha256": "b" * 64,
                  "outcome": "CORRECTIONS_REQUIRED"}
        status = {"correction_rounds": {chapter["unit_id"]: worker.MAX_CORRECTIONS +
                                       worker.MAX_CORRECTION_EXTENSIONS},
                  "deferred_review_units": []}
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, "ROOT", Path(temp)):
            worker.prepare_correction_limit_resume(
                status, {"records": [record]}, chapter)
        self.assertEqual(status["deferred_review_units"], [chapter["unit_id"]])
        self.assertEqual(status["patch_deferral_audit"][0]["review_record_id"], "c" * 32)
        self.assertEqual(chapter["status"], "translated")

    def test_canonical_attention_defer_is_used_for_next_scheduled_unit(self):
        chapter = {"unit_id": "chapter-000025", "number": 25, "status": "translated",
                   "translation_acceptance": {"source_sha256": "a" * 64,
                                              "translation_sha256": "b" * 64}}
        following = {"unit_id": "chapter-000026", "number": 26, "status": "extracted"}
        record = {"unit_id": chapter["unit_id"], "sequence": 1,
                  "record_id": "c" * 32,
                  "outcome": "CORRECTIONS_REQUIRED", "source_sha256": "a" * 64,
                  "translation_sha256": "b" * 64}
        answers = [json.dumps({"operation": "correct_translation", "unit_id":
                              chapter["unit_id"], "chapter_number": 25}),
                   json.dumps({"operation": "translate", "unit_id":
                              following["unit_id"], "chapter_number": 26})]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts" / "workflow").mkdir(parents=True)
            (root / "scripts" / "workflow" / "attention.py").touch()
            status = {"correction_rounds": {chapter["unit_id"]:
                       worker.MAX_CORRECTIONS + worker.MAX_CORRECTION_EXTENSIONS}}
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "chapter_data",
                              return_value=({}, {"records": [record]}, [chapter, following])), \
                 patch.object(worker, "approved_unit_ids", return_value=set()), \
                 patch.object(worker, "cli", side_effect=lambda *cmd:
                              answers.pop(0) if cmd[0] == "resume" else "ok") as cli:
                role, selected = worker.canonical_dispatch("some-book", status)
        self.assertEqual((role, selected["unit_id"]), ("translator", "chapter-000026"))
        cli.assert_any_call("defer-review", "some-book", "25",
                            "--reason", "correction-limit")

    def test_anchored_correction_patches_are_local_and_protect_links(self):
        finding = {"id": "meaning-1", "resolved": False,
                   "translation_anchor": {"quote": "wrong", "occurrence": 0}}
        response = json.dumps({
            "patches": [{"finding_id": "meaning-1", "operation": "replace",
                         "target_anchor": finding["translation_anchor"], "replacement": "right"}],
            "glossary": [], "style_guide": [],
        })
        patches, glossary, style = worker.parse_correction_patches(response, [finding])
        self.assertEqual(worker.apply_correction_patches("before wrong after\n", patches),
                         "before right after\n")
        self.assertEqual((glossary, style), ([], []))
        with self.assertRaisesRegex(worker.JobError, "protected"):
            worker.apply_correction_patches("[wrong](https://example.test)\n", [{
                "operation": "replace", "target_anchor": {
                    "quote": "https://example.test", "occurrence": 0}, "replacement": "https://other.test",
            }])
        with self.assertRaisesRegex(worker.JobError, "overlap"):
            worker.apply_correction_patches("wrong phrase\n", [
                {"operation": "replace", "target_anchor": {"quote": "wrong phrase", "occurrence": 0},
                 "replacement": "right phrase"},
                {"operation": "replace", "target_anchor": {"quote": "phrase", "occurrence": 0},
                 "replacement": "word"},
            ])

    def test_compact_finding_id_patches_and_true_omission_insertion(self):
        findings = [
            {"id": "terminology-5", "resolved": False,
             "translation_anchor": {"quote": "Dear D.", "occurrence": 0}},
            {"id": "omission-10", "resolved": False, "translation_anchor": None},
        ]
        response = json.dumps({
            "patches": [
                {"finding_id": "terminology-5", "operation": "replace",
                 "replacement": "Дорогой Д."},
                {"finding_id": "omission-10", "operation": "insert_after",
                 "insertion_anchor": {"quote": "Next paragraph.", "occurrence": 0},
                 "replacement": " Inserted content."},
            ],
            "glossary": [], "style_guide": [],
        })
        patches, _, _ = worker.parse_correction_patches(response, findings)
        self.assertEqual(patches[0]["target_anchor"], findings[0]["translation_anchor"])
        self.assertEqual(patches[1]["target_anchor"],
                         {"quote": "Next paragraph.", "occurrence": 0})
        self.assertEqual(
            worker.apply_correction_patches("Dear D. Next paragraph.", patches),
            "Дорогой Д. Next paragraph. Inserted content."
        )

    def test_missing_or_ambiguous_omission_anchor_rejects_without_mutation(self):
        finding = {"id": "omission-1", "resolved": False, "translation_anchor": None}
        for patch_data in (
            {"finding_id": "omission-1", "operation": "insert_before",
             "replacement": "content"},
            {"finding_id": "omission-1", "operation": "replace",
             "replacement": "content"},
        ):
            response = json.dumps({"patches": [patch_data],
                                   "glossary": [], "style_guide": []})
            with self.assertRaises(ModelError):
                worker.parse_correction_patches(response, [finding])
        response = json.dumps({"patches": [
            {"finding_id": "omission-1", "operation": "insert_after",
             "insertion_anchor": {"quote": "same", "occurrence": 0},
             "replacement": " content"}
        ], "glossary": [], "style_guide": []})
        patches, _, _ = worker.parse_correction_patches(response, [finding])
        with self.assertRaisesRegex(worker.JobError, "ambiguous"):
            worker.apply_correction_patches("same same", patches)

    def test_ten_finding_batch_binds_existing_review_anchors(self):
        findings = [{"id": f"meaning-{i}", "resolved": False,
                     "translation_anchor": {"quote": f"piece-{i:02d}", "occurrence": 0}}
                    for i in range(1, 11)]
        response = json.dumps({"patches": [
            {"finding_id": f"meaning-{i}", "operation": "replace",
             "replacement": f"fixed-{i}"}
            for i in range(1, 11)
        ], "glossary": [], "style_guide": []})
        patches, _, _ = worker.parse_correction_patches(response, findings)
        before = " ".join(f"piece-{i:02d}" for i in range(1, 11))
        after = worker.apply_correction_patches(before, patches)
        self.assertEqual(after, " ".join(f"fixed-{i}" for i in range(1, 11)))
        self.assertEqual(len(patches), 10)

    def test_stale_review_hash_rejected_before_any_correction_write(self):
        slug, job_id, session = "stale-review-book", "a" * 32, "session"
        unit_id = "chapter-000001"
        chapter = {"unit_id": unit_id, "number": 1, "status": "translated",
                   "translation_path": "translated/unit.md",
                   "translation_acceptance": {"translation_sha256": "a" * 64}}
        ledger = {"records": [{
            "unit_id": unit_id, "sequence": 1, "record_id": "review-1",
            "outcome": "CORRECTIONS_REQUIRED",
            "translation_sha256": "a" * 64,
            "quality_report": {"evidence_version": 2, "findings": [{
                "id": "meaning-1", "resolved": False,
                "translation_anchor": {"quote": "wrong", "occurrence": 0}
            }]},
        }]}
        response = json.dumps({"patches": [
            {"finding_id": "meaning-1", "operation": "replace", "replacement": "right"}
        ], "glossary": [], "style_guide": []})
        status = {"state": "running", "phase": "claimed", "failures": 0,
                  "model_calls_started": 1, "workflow_revision": "b" * 40,
                  "active": {"role": "corrector", "chapter": 1, "session_id": session}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books" / slug
            (book / ".workflow" / "claims").mkdir(parents=True)
            (book / "translated").mkdir()
            target = book / "translated" / "unit.md"
            target.write_text("wrong", encoding="utf-8")
            (book / "glossary.md").write_text("glossary", encoding="utf-8")
            (book / "style-guide.md").write_text("style", encoding="utf-8")
            worker.store_json(book / ".workflow" / "claims" / (unit_id + ".json"),
                              {"session_id": session})
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "chapter_data", return_value=({}, ledger, [chapter])), \
                 patch.object(worker, "context", return_value=("system", "prompt", "source")), \
                 patch.object(worker, "role_contracts", return_value=("contract", ["AGENTS.md"])), \
                 patch.object(worker, "complete", return_value=response), \
                 patch.object(worker, "cli") as cli, patch.object(worker, "checkpoint"):
                worker.process_claim(job_id, slug, status, root)
                self.assertEqual(target.read_text("utf-8"), "wrong")
                self.assertEqual(status["phase"], "model-retry")
                self.assertEqual(status["last_error"],
                                 "correction review is stale; current translation hash changed")
                cli.assert_called_once_with("release", slug, "1", "--session-id", session)

    def test_one_extra_correction_requires_measured_severity_progress(self):
        unit_id = "chapter-000011"
        chapter = {"unit_id": unit_id}

        def record(sequence, translation_sha, severity):
            return {"unit_id": unit_id, "sequence": sequence,
                    "outcome": "CORRECTIONS_REQUIRED", "source_sha256": "a" * 64,
                    "translation_sha256": translation_sha,
                    "quality_report": {"findings": [{"severity": severity, "resolved": False}]}}

        ledger = {"records": [record(1, "b" * 64, "major"),
                              record(2, "c" * 64, "minor")]}
        status = {"correction_rounds": {unit_id: worker.MAX_CORRECTIONS}}
        self.assertTrue(worker.correction_progressed(ledger, chapter))
        self.assertTrue(worker.correction_allowed(status, ledger, chapter))
        status["correction_extensions"] = {unit_id: 1}
        self.assertFalse(worker.correction_allowed(status, ledger, chapter))
        status["correction_extensions"] = {}
        status["correction_rounds"][unit_id] = worker.MAX_CORRECTIONS + 1
        self.assertFalse(worker.correction_allowed(status, ledger, chapter))


    def test_reviewer_anchor_repair_reserves_call_and_revalidates(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        source = "He could leave tomorrow.\n"
        translation = "Он мог уйти завтра.\n"
        report = {
            "evidence_version": 2, "outcome": "CORRECTIONS_REQUIRED",
            "checks": dict.fromkeys(("coverage", "meaning", "terminology",
                                     "style", "structure"), True),
            "findings": [{
                "id": "meaning-1", "category": "meaning", "severity": "major",
                "detail": "Test discrepancy.", "resolved": False,
                "source_anchor": {"quote": "He could leave tomorrow.", "occurrence": 0},
                "translation_anchor": {"quote": "Он, вероятно, мог уйти завтра.", "occurrence": 0},
                "resolves": None,
            }], "style_guide": [],
        }
        bad = json.dumps(report, ensure_ascii=False)
        claim = {"shared_state_revisions": {"glossary": "a" * 64}}
        with self.assertRaises(worker.ReviewerAnchorMismatch) as raised:
            worker.checked_reviewer_response(
                bad, review_context="independent_session", source=source,
                translation=translation.encode(), previous_record=None, claim=claim)
        failure = raised.exception
        self.assertIn("absent", str(failure))
        hints = worker.reviewer_anchor_feedback(failure.report, source, translation)
        self.assertEqual(hints[0]["finding_index"], 0)
        self.assertEqual(hints[0]["field"], "translation_anchor")
        for candidate in hints[0]["exact_current_text_candidates"]:
            self.assertIn(candidate, translation)
        report["findings"][0]["translation_anchor"]["quote"] = "Он мог уйти завтра."
        repaired_answer = json.dumps(report, ensure_ascii=False)
        status = {"state": "running", "model_calls_started": 1, "phase": "model-call-reserved"}
        context = {"role": "reviewer", "review_context": "independent_session"}
        with patch.object(worker, "complete", return_value=repaired_answer) as model, \
             patch.object(worker, "checkpoint") as checkpoint, \
             patch.object(worker, "model_call_limit", return_value=3):
            repaired = worker.retry_reviewer_anchor_evidence(
                "b" * 32, "unit-book", status, context, "system",
                "CURRENT SOURCE:\n" + source + "\nCURRENT TRANSLATION:\n" + translation,
                bad, failure, source, translation.encode("utf-8"))
        outcome, checked, _ = worker.checked_reviewer_response(
            repaired, review_context="independent_session", source=source,
            translation=translation.encode(), previous_record=None, claim=claim)
        self.assertEqual(outcome, "CORRECTIONS_REQUIRED")
        self.assertEqual(checked["findings"][0]["translation_anchor"]["quote"], "Он мог уйти завтра.")
        self.assertEqual(status["model_calls_started"], 2)
        self.assertEqual(status["phase"], "review-anchor-repair-reserved")
        self.assertEqual(status["prompt_context"]["anchor_repair"], "exact-quote-validation")
        self.assertEqual(checkpoint.call_count, 1)
        self.assertEqual(model.call_count, 1)
        self.assertIn("REJECTED REVIEW", model.call_args.args[2])
        self.assertNotIn("Он, вероятно, мог уйти завтра.", json.dumps(status, ensure_ascii=False))

    def test_reviewer_anchor_repair_respects_model_budget_and_does_not_accept_fakes(self):
        source = "Only verified passage.\n"
        translation = "Только проверенный фрагмент.\n"
        report = {"findings": [{
            "id": "style-1", "source_anchor": {"quote": "Only verified passage.", "occurrence": 0},
            "translation_anchor": {"quote": "Фантазия модели.", "occurrence": 0}
        }]}
        failure = worker.ReviewerAnchorMismatch(
            "findings[0].translation_anchor: quoted passage occurrence 0 is absent",
            report)
        status = {"model_calls_started": 4, "phase": "model-call-reserved"}
        with patch.object(worker, "complete") as model, \
             patch.object(worker, "checkpoint") as checkpoint, \
             patch.object(worker, "model_call_limit", return_value=4):
            with self.assertRaises(worker.ReviewerAnchorMismatch):
                worker.retry_reviewer_anchor_evidence(
                    "c" * 32, "unit-book", status, {}, "system", "user",
                    json.dumps(report), failure, source, translation.encode())
        self.assertEqual(status["model_calls_started"], 4)
        self.assertEqual(status["phase"], "model-call-reserved")
        model.assert_not_called()
        checkpoint.assert_not_called()


    def test_reviewer_json_schema_repair_uses_one_bounded_verified_call(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        source, translation = "He returned.\n", "Он вернулся.\n"
        bad = '{"unrecognized_result": "not an anchored review"}'
        claim = {"shared_state_revisions": {"glossary": "b" * 64}}
        with self.assertRaisesRegex(ModelError, "invalid literary memory schema") as raised:
            worker.checked_reviewer_response(
                bad, review_context="independent_session", source=source,
                translation=translation.encode(), previous_record=None, claim=claim)
        valid = json.dumps({
            "evidence_version": 2, "outcome": "PASS",
            "checks": dict.fromkeys(("coverage", "meaning", "terminology",
                                     "style", "structure"), True),
            "findings": [], "style_guide": [],
        }, ensure_ascii=False)
        status = {"state": "running", "model_calls_started": 3, "phase": "model-call-reserved"}
        context = {"role": "reviewer", "review_context": "independent_session"}
        user = "CURRENT SOURCE:\n" + source + "\nCURRENT TRANSLATION:\n" + translation
        with patch.object(worker, "complete", return_value=valid) as model, \
             patch.object(worker, "checkpoint") as checkpoint, \
             patch.object(worker, "model_call_limit", return_value=4):
            repaired = worker.retry_reviewer_anchor_evidence(
                "b" * 32, "unit-book", status, context, "system",
                user, bad, raised.exception, source, translation.encode())
        self.assertEqual(
            worker.checked_reviewer_response(
                repaired, review_context="independent_session", source=source,
                translation=translation.encode(), previous_record=None, claim=claim)[0],
            "PASS")
        self.assertEqual(status["model_calls_started"], 4)
        self.assertEqual(status["phase"], "review-schema-repair-reserved")
        self.assertEqual(status["prompt_context"]["schema_repair"], "review-report-validation")
        checkpoint.assert_called_once()
        model.assert_called_once()
        self.assertIn("JSON-SCHEMA", model.call_args.args[2])
        self.assertNotIn("unrecognized_result", json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
