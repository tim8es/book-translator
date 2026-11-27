"""Tests for the Chat request envelope, failure atomicity and durable receipts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chat_ops_bridge.py"
spec = importlib.util.spec_from_file_location("chat_ops_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

RID = "0123456789abcdef0123456789abcdef"
REV = "8f70b1c1c108926599c36ce40021860bed581400"


def sample(operation="claim"):
    base = {"schema_version": 1, "request_id": RID, "book_slug": "test-book",
            "workflow_revision": REV, "operation": operation,
            "chapter": 4, "session_id": "chat-test-session"}
    if operation == "claim":
        base["role"] = "translator"
    if operation == "accept-translation":
        base.update({"translation": "Русский текст\n",
                     "translation_sha256": hashlib.sha256("Русский текст\n".encode()).hexdigest(),
                     "source_sha256": "a" * 64, "claim_sha256": "b" * 64})
    if operation == "proposal-submit":
        base.update({"role": "translator", "target": "glossary",
                     "suggestion": "# Glossary\nnew\n", "rationale": "Source-supported decision",
                     "source_sha256": "a" * 64, "translation_sha256": "c" * 64,
                     "claim_sha256": "b" * 64})
    if operation == "proposal-reconcile":
        base.update({"proposal_id": "d" * 32, "decision": "accept"})
    if operation == "defer-review":
        base["reason"] = "correction-limit"
    if operation == "review-record":
        base.update({"outcome": "PASS", "quality_report": {"checks": {}},
                     "source_sha256": "a" * 64, "translation_sha256": "c" * 64,
                     "claim_sha256": "b" * 64})
    return base


def run(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class RequestTests(unittest.TestCase):
    def test_allowlisted_operations(self):
        for op in sorted(bridge.OPERATIONS):
            with self.subTest(op=op):
                self.assertEqual(bridge.validate(sample(op), RID)["operation"], op)

    def test_scoped_pending_isolated_by_book_and_invalid_envelopes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / bridge.REQUESTS
            directory.mkdir(parents=True)
            for i, slug in enumerate(("book-one", "book-two", "../bad"), start=1):
                payload = sample()
                payload["book_slug"] = slug
                (directory / f"{i:032x}.json").write_text(
                    json.dumps(payload), encoding="utf-8")
            self.assertEqual(bridge.pending(root, "book-one"), [f"{1:032x}"])
            self.assertEqual(bridge.pending(root, "book-two"), [f"{2:032x}"])
            self.assertEqual(bridge.pending(root, "__invalid__"), [f"{3:032x}"])
            (root / bridge.RESULTS).mkdir()
            (root / bridge.RESULTS / f"{1:032x}.json").write_text("{}", encoding="utf-8")
            self.assertEqual(bridge.pending(root, "book-one"), [])

    def test_unicode_book_slug_passes_same_safe_canonical_rules(self):
        payload = sample("resume-auto")
        payload["book_slug"] = "без-названия"
        self.assertEqual(bridge.validate(payload, RID)["book_slug"], "без-названия")
        for unsafe in ("../bad", "_under", "-bad", "dir/child", "some.name", ""):
            with self.subTest(slug=unsafe), self.assertRaisesRegex(
                    bridge.RequestError, "unsafe book slug"):
                payload["book_slug"] = unsafe
                bridge.validate(payload, RID)

    def test_auto_mode_switch_does_not_edit_chapter_or_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books" / "test-book"
            book.mkdir(parents=True)
            (book / "metadata.json").write_text(
                json.dumps({"workflow": {"resolved_revision": REV}}), encoding="utf-8")
            for operation, mode in (("pause-auto", "chatops"), ("resume-auto", "auto")):
                result = bridge.execute(root, root, sample(operation))
                self.assertEqual(result, {"auto_mode": mode})
                control = json.loads((book / ".workflow/auto-control.json").read_text())
                self.assertEqual(control["mode"], mode)
            self.assertFalse((book / "progress.json").exists())
            self.assertFalse((book / ".workflow" / "claims").exists())

    def test_pinned_engine_bootstraps_state_only_repository(self):
        """An ignored ephemeral install is sufficient; no tracked fork needed."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, engine = root / "book-state", root / "engine"
            state.mkdir()
            (state / "books").mkdir()
            run("init", str(state))
            (engine / "scripts").mkdir(parents=True)
            (engine / "docs").mkdir()
            (state / "docs/migrations").mkdir(parents=True)
            (state / "docs/migrations/evidence.json").write_text(
                '{"historic":true}', encoding="utf-8")
            (engine / "scripts/book.py").write_text("# pinned CLI", encoding="utf-8")
            (engine / "scripts/corpus.py").write_text("# pinned corpus", encoding="utf-8")
            (engine / "docs/TRANSLATION.md").write_text(
                "Pinned translation rules", encoding="utf-8")
            for filename in ("AGENTS.md", "agent-manifest.json", "SKILL.md"):
                (engine / filename).write_text("pinned", encoding="utf-8")
            run("init", str(engine))
            run("-C", str(engine), "config", "user.name", "Integration Test")
            run("-C", str(engine), "config", "user.email", "dev@example.invalid")
            run("-C", str(engine), "add", "-A")
            run("-C", str(engine), "commit", "-m", "synthetic revision")
            revision = run("-C", str(engine), "rev-parse", "HEAD").stdout.strip()
            bridge.pinned_engine(state, engine, revision)
            install = json.loads(
                (engine / ".book-translator-install.json").read_text("utf-8"))
            self.assertEqual(install["resolved_revision"], revision)
            self.assertEqual(install["requested_ref"], revision)
            self.assertFalse((state / "scripts").exists())
            self.assertFalse((state / "agent-manifest.json").exists())
            self.assertFalse((state / ".book-translator-install.json").exists())
            self.assertTrue((engine / "books").is_symlink())
            self.assertTrue((engine / "agent-manifest.json").is_file())
            self.assertEqual(
                (state / "docs/migrations/evidence.json").read_text("utf-8"),
                '{"historic":true}')
            self.assertIn("Pinned translation rules",
                          (engine / "docs/TRANSLATION.md").read_text("utf-8"))
            bridge.pinned_engine(state, engine, revision)  # idempotent
            install_marker = engine / ".book-translator-install.json"
            self.assertEqual(json.loads(install_marker.read_text("utf-8"))
                             ["resolved_revision"], revision)
            install_marker.unlink()
            install_marker.symlink_to(state / "docs/migrations/evidence.json")
            with self.assertRaisesRegex(bridge.RequestError, "unsafe pinned installation marker"):
                bridge.pinned_engine(state, engine, revision)
            self.assertFalse((state / ".book-translator-install.json").exists())
            self.assertEqual(
                (state / "docs/migrations/evidence.json").read_text("utf-8"),
                '{"historic":true}')

    def test_defer_review_is_allowlisted_and_requires_reason(self):
        payload = sample("defer-review")
        self.assertEqual(bridge.validate(payload, RID)["reason"], "correction-limit")
        for invalid in (None, "arbitrary-shell", 42, ""):
            bad = dict(payload)
            if invalid is None:
                bad.pop("reason")
            else:
                bad["reason"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(bridge.RequestError):
                bridge.validate(bad, RID)

    def test_defer_review_runs_only_canonical_pinned_command(self):
        payload = bridge.validate(sample("defer-review"), RID)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(bridge, "expected_book", return_value=(root, {})), \
                 patch.object(bridge, "pinned_engine"), \
                 patch.object(bridge, "cli", return_value={"unit_id": "chapter-000004"}) as cli:
                result = bridge.execute(root, root, payload)
            self.assertEqual(result["unit_id"], "chapter-000004")
            cli.assert_any_call(root, "book.py", "defer-review", "test-book", "4",
                                "--reason", "correction-limit", "--json")

    def test_proposal_request_schema_rejects_unsafe_or_unbound_fields(self):
        for field, value in (("role", "orchestrator"), ("target", "../metadata.json"),
                             ("suggestion", ""), ("rationale", ""), ("claim_sha256", "bad"),
                             ("translation_sha256", "bad"), ("source_sha256", "bad")):
            with self.subTest(field=field), self.assertRaises(bridge.RequestError):
                obj = sample("proposal-submit")
                obj[field] = value
                bridge.validate(obj, RID)
        for field, value in (("proposal_id", "../x"), ("decision", "write"),
                             ("replacement", "arbitrary replacement")):
            with self.subTest(field=field), self.assertRaises(bridge.RequestError):
                obj = sample("proposal-reconcile")
                obj[field] = value
                bridge.validate(obj, RID)
        rejected = sample("proposal-reconcile")
        rejected["decision"] = "reject"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(rejected, RID)
        rejected["reason"] = "Not supported by the source."
        self.assertEqual(bridge.validate(rejected, RID), rejected)

    def test_proposal_reconcile_runs_pinned_transport_only(self):
        payload = bridge.validate(sample("proposal-reconcile"), RID)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(bridge, "expected_book", return_value=(root, {})), \
                 patch.object(bridge, "pinned_engine"), \
                 patch.object(bridge, "cli"), \
                 patch.object(bridge, "proposal_cli", return_value={"status": "accepted"}) as command:
                self.assertEqual(bridge.execute(root, root, payload), {"status": "accepted"})
            command.assert_called_once_with(root, payload)

    def test_proposal_submit_checks_live_claim_and_accepted_translation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "translated").mkdir()
            artifact = root / "translated/004.md"
            artifact.write_bytes(b"translation\n")
            payload = sample("proposal-submit")
            payload["translation_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            with patch.object(bridge, "expected_book", return_value=(root, {})), \
                 patch.object(bridge, "pinned_engine"), patch.object(bridge, "cli"), \
                 patch.object(bridge, "chapter_and_claim", return_value={
                     "status": "translated", "translation_path": "translated/004.md"}) as claim, \
                 patch.object(bridge, "proposal_cli", return_value={"proposal_id": "d" * 32}) as command:
                self.assertEqual(bridge.execute(root, root, payload)["proposal_id"], "d" * 32)
                claim.assert_called_once_with(root, payload, role="translator")
                command.assert_called_once_with(root, payload)
                artifact.write_bytes(b"changed\n")
                with self.assertRaisesRegex(bridge.RequestError, "translation artifact changed"):
                    bridge.execute(root, root, payload)
                self.assertEqual(command.call_count, 1)

    def test_pinned_proposal_manager_submit_accept_stale_and_scope(self):
        # Real pinned filesystem manager; no mock shared-state mutation.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = root / "books/test-book"
            book.mkdir(parents=True)
            (book / "metadata.json").write_text(json.dumps({
                "workflow": {"resolved_revision": REV}}), encoding="utf-8")
            (book / "progress.json").write_text(json.dumps({"chapters": [
                {"number": 4, "unit_id": "chapter-000004"},
                {"number": 5, "unit_id": "chapter-000005"}]}), encoding="utf-8")
            glossary = book / "glossary.md"
            style = book / "style-guide.md"
            glossary.write_text("# Glossary\n", encoding="utf-8")
            style.write_text("# Style Guide\n", encoding="utf-8")
            claim_path = book / ".workflow/claims/chapter-000004.json"
            claim_path.parent.mkdir(parents=True)
            claim = {"schema_version": 1, "claim_id": "c" * 32,
                     "unit_id": "chapter-000004", "role": "translator",
                     "session_id": "chat-test-session", "base_revision": "base-revision",
                     "base_commit": "base-commit", "workflow_revision": REV,
                     "shared_state_revisions": {
                         "glossary": hashlib.sha256(glossary.read_bytes()).hexdigest(),
                         "style_guide": hashlib.sha256(style.read_bytes()).hexdigest()},
                     "claimed_at": "2020-01-01T00:00:00Z",
                     "expires_at": "2099-01-01T00:00:00Z"}
            claim_path.write_text(json.dumps(claim), encoding="utf-8")
            def pinned(payload, ok=True):
                request_path = root / "request.json"
                request_path.write_text(json.dumps(payload), encoding="utf-8")
                # A fresh process imports the exact engine's modules just as
                # the runtime adapter does, without a network checkout.
                command = (
                    "import sys; from pathlib import Path; "
                    "sys.path.insert(0, sys.argv[1]); "
                    "from pinned_cli import run_proposal; "
                    "run_proposal(Path(sys.argv[2]), sys.argv[3], [sys.argv[4]])"
                )
                result = subprocess.run(
                    [sys.executable, "-c", command, str(SCRIPT.parent),
                     str(root), REV, str(request_path)], capture_output=True, text=True)
                if ok:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)
                self.assertNotEqual(result.returncode, 0)
                return result
            submit = sample("proposal-submit")
            submit["suggestion"] = "# Glossary\n| Original | Translation | Type | Notes |\n| --- | --- | --- | --- |\n| Moon | Луна | term | |\n"
            first = pinned(submit)
            second = pinned(dict(submit, target="style_guide", suggestion="# Style Guide\nNew evidence.\n"))
            self.assertEqual(glossary.read_text(), "# Glossary\n")
            accept = dict(sample("proposal-reconcile"), proposal_id=first["proposal_id"],
                          session_id="orchestrator-chat")
            pinned(dict(accept, chapter=5), ok=False)
            self.assertEqual(pinned(accept)["status"], "accepted")
            self.assertEqual(glossary.read_text(), submit["suggestion"])
            self.assertEqual(pinned(accept)["status"], "accepted")
            self.assertEqual(pinned(dict(accept, proposal_id=second["proposal_id"]))["status"], "stale")
            self.assertEqual(style.read_text(), "# Style Guide\n")
            pinned(submit, ok=False)
            self.assertEqual(len(list((book / ".workflow/proposals").glob("*.resolution.json"))), 2)

    def test_unknown_operation_fails(self):
        obj = sample()
        obj["operation"] = "shell"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_wrong_name_and_path_traversal_fail(self):
        for field, value in [("request_id", "f" * 32), ("book_slug", "../other"),
                             ("session_id", "s; whoami"), ("chapter", True)]:
            obj = sample()
            obj[field] = value
            with self.subTest(field=field), self.assertRaises(bridge.RequestError):
                bridge.validate(obj, RID)

    def test_unexpected_fields_fail_closed(self):
        obj = sample()
        obj["command"] = "rm -rf /"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_tampered_translation_fails(self):
        obj = sample("accept-translation")
        obj["translation"] += "mismatch"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_reviewer_requires_evidence(self):
        obj = sample("review-record")
        del obj["quality_report"]
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_parallel_claim_snapshot_requires_sha(self):
        obj = sample()
        obj["shared_state"] = {"glossary": "a" * 40, "style_guide": "b" * 40}
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)
        obj["base_commit"] = "c" * 40
        bridge.validate(obj, RID)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "r.json"
            p.write_text('{"request_id":"a","request_id":"b"}', encoding="utf-8")
            with self.assertRaises(bridge.RequestError):
                bridge.strict_json(p)

    def test_mode_handoff_process_does_not_require_unpinned_corpus_preflight(self):
        """The transport commit must succeed even if global engine differs."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bare, work = root / "remote.git", root / "work"
            run("init", "--bare", str(bare))
            run("clone", str(bare), str(work))
            run("-C", str(work), "checkout", "-b", "main")
            run("-C", str(work), "config", "user.name", "Unit Test")
            run("-C", str(work), "config", "user.email", "test@example.invalid")
            request_path = work / bridge.REQUESTS / (RID + ".json")
            request_path.parent.mkdir(parents=True)
            request_path.write_text(json.dumps(sample("resume-auto")), encoding="utf-8")
            book = work / "books" / "test-book"
            book.mkdir(parents=True)
            (book / "metadata.json").write_text(json.dumps({
                "workflow": {"resolved_revision": REV}
            }), encoding="utf-8")
            run("-C", str(work), "add", "-A")
            run("-C", str(work), "commit", "-m", "seed book and request")
            run("-C", str(work), "push", "-u", "origin", "main")
            with patch.object(bridge, "cli", side_effect=AssertionError(
                    "No unpinned literary CLI may run for a control-only request")):
                self.assertEqual(bridge.process(work, root / "engine", RID), "ok")
            control = json.loads((book / ".workflow" / "auto-control.json").read_text())
            self.assertEqual(control["mode"], "auto")
            receipt = json.loads((work / bridge.RESULTS / (RID + ".json")).read_text())
            self.assertEqual(receipt["status"], "ok")
            self.assertEqual(bridge.process(work, root / "engine", RID), "already-recorded")

    def test_publish_failed_receipt_without_book_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bare = root / "remote.git"
            work = root / "work"
            engine = root / "engine"
            run("init", "--bare", str(bare))
            run("clone", str(bare), str(work))
            run("-C", str(work), "checkout", "-b", "main")
            run("-C", str(work), "config", "user.name", "Unit Test")
            run("-C", str(work), "config", "user.email", "test@example.invalid")
            request_path = work / bridge.REQUESTS / (RID + ".json")
            request_path.parent.mkdir(parents=True)
            request_path.write_text(json.dumps(sample()), encoding="utf-8")
            book = work / "books" / "test-book"
            book.mkdir(parents=True)
            (book / "metadata.json").write_text(
                '{"workflow":{"resolved_revision":"' + "0" * 40 + '"}}',
                encoding="utf-8")
            before = (book / "metadata.json").read_bytes()
            run("-C", str(work), "add", "-A")
            run("-C", str(work), "commit", "-m", "seed requests")
            run("-C", str(work), "push", "-u", "origin", "main")
            self.assertEqual(bridge.process(work, engine, RID), "failed")
            self.assertEqual(bridge.process(work, engine, RID), "already-recorded")
            receipt = json.loads((work / bridge.RESULTS / (RID + ".json")).read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("revision", receipt["error"])
            self.assertEqual((book / "metadata.json").read_bytes(), before)
            self.assertFalse((book / ".workflow").exists())


    def test_real_engine_claim_accept_review_cycle_with_remote_receipts(self):
        """A disposable Git remote exercises the actual pinned CLI, not mocks."""
        import shutil
        import sys

        public = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, work, engine = root / "remote.git", root / "books-repo", root / "engine"
            run("init", "--bare", str(remote))
            run("clone", str(remote), str(work))
            run("clone", str(public), str(engine))
            rev = run("-C", str(engine), "rev-parse", "HEAD").stdout.strip()
            shutil.copytree(public / "scripts", work / "scripts")
            shutil.copy2(public / "agent-manifest.json", work / "agent-manifest.json")
            (work / ".book-translator-install.json").write_text(json.dumps({
                "schema_version": 1,
                "canonical_repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "main", "resolved_revision": rev, "install_root": "."
            }), encoding="utf-8")
            src = work / "novel.md"
            src.write_text("# First\n\nSome original English prose.\n", encoding="utf-8")
            extract = subprocess.run(
                [sys.executable, str(work / "scripts/book.py"), "extract", str(src),
                 "--slug", "novel", "--target-language", "ru"],
                cwd=work, text=True, capture_output=True)
            self.assertEqual(extract.returncode, 0, extract.stderr)
            seal = subprocess.run(
                [sys.executable, str(work / "scripts/corpus.py"), "seal", "novel"],
                cwd=work, text=True, capture_output=True)
            self.assertEqual(seal.returncode, 0, seal.stderr)
            # Mimic Obsession: book pin is older than the repository-wide
            # installation marker. The runner must not publish this override.
            install_path = work / ".book-translator-install.json"
            global_pin = "0" * 40
            installation = json.loads(install_path.read_text("utf-8"))
            installation["resolved_revision"] = global_pin
            install_path.write_text(json.dumps(installation), encoding="utf-8")
            # Git omits empty directories. Keep the canonical workspace layout
            # in the remote fixture without bypassing book.py validation.
            for folder in ("translated", "output"):
                directory = work / "books" / "novel" / folder
                directory.mkdir(exist_ok=True)
                (directory / ".gitkeep").write_text("", encoding="utf-8")
            run("-C", str(work), "checkout", "-b", "main")
            run("-C", str(work), "config", "user.name", "Bridge Test")
            run("-C", str(work), "config", "user.email", "bridge@example.invalid")
            run("-C", str(work), "add", "-A")
            run("-C", str(work), "commit", "-m", "book fixture")
            run("-C", str(work), "push", "-u", "origin", "main")

            book = work / "books" / "novel"
            count = 0

            def issue(op, session, **extra):
                nonlocal count
                count += 1
                rid = f"{count:032x}"
                payload = {"schema_version": 1, "request_id": rid,
                           "book_slug": "novel", "workflow_revision": rev,
                           "operation": op, "chapter": 1, "session_id": session}
                payload.update(extra)
                file = work / bridge.REQUESTS / (rid + ".json")
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                run("-C", str(work), "add", "-A")
                run("-C", str(work), "commit", "-m", "request " + rid)
                run("-C", str(work), "push", "origin", "HEAD:main")
                status = bridge.process(work, engine, rid)
                receipt = json.loads((work / bridge.RESULTS / (rid + ".json")).read_text())
                self.assertEqual(receipt["status"], status)
                self.assertEqual(bridge.process(work, engine, rid), "already-recorded")
                return receipt

            claim = issue("claim", "writer-1", role="translator")
            self.assertEqual(claim["status"], "ok", claim["error"])
            conflict = issue("claim", "writer-2", role="translator")
            self.assertEqual(conflict["status"], "failed")
            progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
            chapter = progress["chapters"][0]
            claim_file = book / ".workflow/claims" / (chapter["unit_id"] + ".json")
            source_hash = hashlib.sha256((book / chapter["source_path"]).read_bytes()).hexdigest()
            claim_hash = hashlib.sha256(claim_file.read_bytes()).hexdigest()

            bad = issue("accept-translation", "writer-1", translation="# Ошибка\n",
                        translation_sha256=hashlib.sha256("# Ошибка\n".encode()).hexdigest(),
                        source_sha256=source_hash, claim_sha256="0" * 64)
            self.assertEqual(bad["status"], "failed")
            self.assertEqual(json.loads((book / "progress.json").read_text())[
                "chapters"][0]["status"], "extracted")

            translated = "# Первая глава\n\nНебольшой полный перевод.\n"
            accepted = issue("accept-translation", "writer-1",
                             translation=translated,
                             translation_sha256=hashlib.sha256(translated.encode()).hexdigest(),
                             source_sha256=source_hash, claim_sha256=claim_hash)
            self.assertEqual(accepted["status"], "ok", accepted["error"])
            self.assertEqual(json.loads((book / "progress.json").read_text())[
                "chapters"][0]["status"], "translated")
            self.assertEqual(issue("release", "writer-1")["status"], "ok")
            self.assertEqual(issue("claim", "reviewer-1", role="reviewer")["status"], "ok")
            claim_hash = hashlib.sha256(claim_file.read_bytes()).hexdigest()
            recorded = issue("review-record", "reviewer-1", outcome="PASS",
                             quality_report={"context": "bounded_context",
                                             "checks": {"coverage": True, "meaning": True,
                                                        "terminology": True, "style": True,
                                                        "structure": True}, "findings": []},
                             source_sha256=source_hash, claim_sha256=claim_hash,
                             translation_sha256=hashlib.sha256(translated.encode()).hexdigest())
            self.assertEqual(recorded["status"], "ok", recorded["error"])
            self.assertEqual(issue("accept-review", "orchestrator-1")["status"], "ok")
            self.assertEqual(issue("release", "reviewer-1")["status"], "ok")
            self.assertEqual(json.loads((book / "progress.json").read_text())[
                "chapters"][0]["status"], "reviewed")
            # The engine override was runner-local; persisted global provenance
            # remains pinned to the original installation revision.
            self.assertEqual(json.loads(install_path.read_text("utf-8"))[
                "resolved_revision"], global_pin)
            self.assertEqual(json.loads(run("-C", str(work), "show",
                                            "HEAD:.book-translator-install.json").stdout)[
                "resolved_revision"], global_pin)

            # Exercise the actual exporter against the completed canonical
            # state, not a fabricated status payload. The fixture PASS is
            # mechanical test data, not real literary review evidence.
            sys.path.insert(0, str(public / "automation"))
            try:
                import blind_quality_export as export
                import paired_quality_qualification as paired
                snapshot = export.checked_snapshot(work, "novel",
                                                   export.canonical_preflight)
                self.assertEqual(snapshot["workflow_revision"], rev)
                self.assertEqual(len(snapshot["units"]), 1)
                self.assertEqual(snapshot["units"][0]["source"]["sha256"], source_hash)
                self.assertEqual(snapshot["units"][0]["candidate"]["text"], translated)
                self.assertEqual(snapshot["commit"], run("-C", str(work),
                    "rev-parse", "HEAD").stdout.strip())
                # An ordinary same-chat fallback review is valid canonical
                # evidence but cannot satisfy the independent paired benchmark.
                chapters = json.loads((book / "progress.json").read_text("utf-8"))["chapters"]
                ledger = json.loads((book / "review-ledger.json").read_text("utf-8"))["records"]
                receipts = list(paired.successful_receipts(work, "novel", rev))
                with self.assertRaisesRegex(ValueError, "independent ChatOps review context"):
                    paired.verify_receipts(chapters, ledger, receipts)
            finally:
                sys.path.remove(str(public / "automation"))


if __name__ == "__main__":
    unittest.main()
