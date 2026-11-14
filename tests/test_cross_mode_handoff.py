"""Synthetic Auto -> ChatOps -> Auto book handoff with canonical real CLI.

No model API, user source, hosted queue or external Git service is required.
The modes share a byte-verified book and the same immutable literary pin.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PUBLIC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PUBLIC / "automation"))
sys.path.insert(0, str(PUBLIC / "scripts"))
import book_runner as auto
import chat_ops_bridge as chat


class CrossModeHandoffTests(unittest.TestCase):
    def test_auto_to_chatops_to_auto_same_book_no_fabricated_review(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "book-state"
            state.mkdir()
            shutil.copytree(PUBLIC / "scripts", state / "scripts")
            shutil.copy2(PUBLIC / "agent-manifest.json",
                         state / "agent-manifest.json")
            pin = "a" * 40
            (state / ".book-translator-install.json").write_text(
                json.dumps({"schema_version": 1,
                            "canonical_repository":
                                "https://github.com/tim8es/book-translator",
                            "install_root": ".",
                            "requested_ref": pin, "resolved_revision": pin}),
                encoding="utf-8")
            original = ("# Chapter One\n\nMara visited Northbridge.\n"
                        "\n# Chapter Two\n\nTheo met Mara in Northbridge.\n")
            source = state / "synthetic.md"
            source.write_text(original, encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(state / "scripts/book.py"),
                "extract", str(source), "--slug", "handoff-book",
                "--target-language", "ru"],
                cwd=state, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            seal = subprocess.run([
                sys.executable, str(state / "scripts/corpus.py"),
                "seal", "handoff-book"], cwd=state,
                capture_output=True, text=True)
            self.assertEqual(seal.returncode, 0, seal.stderr)

            book = state / "books" / "handoff-book"
            status = {"schema_version": 1, "state": "running",
                      "workflow_revision": pin, "failures": 0,
                      "correction_rounds": {}}
            model_roles = []
            translations = {
                1: "# Первая глава\n\nМара побывала в Нортбридже.\n",
                2: "# Вторая глава\n\nТео встретил Мару в Нортбридже.\n",
            }
            current = {"chapter": 1}

            def model(role, system, prompt):
                model_roles.append((current["chapter"], role))
                if role == "TRANSLATOR":
                    self.assertEqual(current["chapter"], 1)
                    return json.dumps({
                        "translation": translations[1],
                        "glossary": [], "style_guide": []}, ensure_ascii=False)
                if role == "REVIEWER":
                    return json.dumps({
                        "evidence_version": 2, "outcome": "PASS",
                        "checks": {"coverage": True, "meaning": True,
                                   "terminology": True, "style": True,
                                   "structure": True},
                        "findings": [], "style_guide": [],
                    })
                raise AssertionError("Unexpected model role " + role)

            def chat_request(operation, **extra):
                request = {
                    "schema_version": 1,
                    "request_id": "c" * 32,
                    "book_slug": "handoff-book",
                    "workflow_revision": pin,
                    "chapter": 2,
                    "session_id": "chat-writer-2",
                    "operation": operation,
                    **extra,
                }
                return chat.execute(state, PUBLIC, chat.validate(
                    request, request["request_id"]))

            with patch.object(auto, "ROOT", state), \
                 patch.object(auto, "git", return_value="b" * 40), \
                 patch.object(auto, "checkpoint"), \
                 patch.object(auto, "complete", side_effect=model):
                auto.verify("handoff-book")
                chapters = auto.chapter_data("handoff-book")[2]
                self.assertEqual(len(chapters), 2)
                for role in ("translator", "reviewer"):
                    auto.claim("f" * 32, "handoff-book", status, role, chapters[0])
                    auto.process_claim("f" * 32, "handoff-book", status, PUBLIC)
                self.assertEqual(auto.chapter_data("handoff-book")[2][0]["status"],
                                 "reviewed")
                self.assertEqual(auto.canonical_dispatch(
                    "handoff-book", status)[0], "translator")

                self.assertEqual(chat_request("pause-auto"),
                                 {"auto_mode": "chatops"})
                self.assertEqual(auto.auto_mode("handoff-book"), "chatops")
                self.assertEqual(auto.chapter_data("handoff-book")[2][1]["status"],
                                 "extracted")

                # Exact pinned canonical commands; no model or fabricated review.
                with patch.object(chat, "pinned_engine") as pinned:
                    claimed = chat_request("claim", role="translator")
                    self.assertIsInstance(claimed, dict)
                    chapter = auto.chapter_data("handoff-book")[2][1]
                    claim_file = (book / ".workflow" / "claims" /
                                  (chapter["unit_id"] + ".json"))
                    self.assertTrue(claim_file.is_file())
                    original_hash = hashlib.sha256(
                        (book / chapter["source_path"]).read_bytes()).hexdigest()
                    translated = translations[2]
                    self.assertIsInstance(chat_request(
                        "accept-translation", translation=translated,
                        translation_sha256=hashlib.sha256(
                            translated.encode("utf-8")).hexdigest(),
                        source_sha256=original_hash,
                        claim_sha256=hashlib.sha256(
                            claim_file.read_bytes()).hexdigest()), dict)
                    chat_request("release")
                    self.assertEqual(pinned.call_count, 3)

                self.assertEqual(auto.chapter_data("handoff-book")[2][1]["status"],
                                 "translated")
                self.assertEqual(chat_request("resume-auto"), {"auto_mode": "auto"})
                self.assertEqual(auto.auto_mode("handoff-book"), "auto")
                operation, chapter = auto.canonical_dispatch("handoff-book", status)
                self.assertEqual((operation, chapter["number"]), ("reviewer", 2))
                self.assertFalse((book / ".workflow/claims" /
                                  (chapter["unit_id"] + ".json")).exists())

                current["chapter"] = 2
                auto.claim("f" * 32, "handoff-book", status, "reviewer", chapter)
                auto.process_claim("f" * 32, "handoff-book", status, PUBLIC)
                self.assertEqual(
                    [item["status"] for item in auto.chapter_data(
                        "handoff-book")[2]], ["reviewed", "reviewed"])
                self.assertEqual(model_roles, [
                    (1, "TRANSLATOR"), (1, "REVIEWER"), (2, "REVIEWER")])
                self.assertEqual(source.read_text("utf-8"), original)
                self.assertEqual(
                    json.loads((book / "metadata.json").read_text("utf-8"))
                    ["workflow"]["resolved_revision"], pin)


if __name__ == "__main__":
    unittest.main()
