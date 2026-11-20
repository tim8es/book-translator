"""Runtime integration tests for durable transactional gates, no paid calls."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner
import candidate_review
import transactional_quality as quality

def marked(parts):
    return "".join("<!-- bt-block:%06d -->\n%s\n\n" % (i, part)
                   for i, part in enumerate(parts, 1))

def verdict(ids, rejected=()):
    return json.dumps({"schema_version":1,
        "checks":dict.fromkeys(candidate_review.CHECKS,True),
        "decisions":[{"finding_id":fid,
                      "verdict":"reject" if fid in rejected else "approve",
                      "risk":"meaning" if fid in rejected else "none"} for fid in ids]})

class TransactionalGateRuntimeTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.book=self.root/"books"/"synthetic"
        self.book.mkdir(parents=True)
        (self.book/"glossary.md").write_text("",encoding="utf-8")
        (self.book/"style-guide.md").write_text("",encoding="utf-8")
        (self.book/"metadata.json").write_text(json.dumps({"source_language":"en","target_language":"ru"}),encoding="utf-8")
        self.source=marked(["Source token %02d."%n for n in range(1,21)])
        self.old=marked(["Old token %02d."%n for n in range(1,21)])
        self.path=self.book/"translated.md"
        self.path.write_bytes(self.old.encode("utf-8"))
        self.stage=self.book/"stage.json"
        self.chapter={"unit_id":"chapter-000001","number":1}
        self.status={"workflow_revision":"a"*40,"model_calls_started":0,
                     "active":{"session_id":"test","role":"corrector","unit_id":"chapter-000001"}}
        for ctx in (patch.dict(os.environ,{"BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY":"on"}),
                    patch.object(runner,"ROOT",self.root),
                    patch.object(runner,"role_contracts",return_value=("Pinned independent review",["AGENTS.md"])),
                    patch.object(runner,"checkpoint")):
            ctx.start();self.addCleanup(ctx.stop)
    def fixture(self, numbers):
        findings=[]; patches=[]
        for n in numbers:
            fid="fix-%02d"%n
            ta={"quote":"Old token %02d."%n,"occurrence":0}
            findings.append({"id":fid,"category":"meaning","severity":"minor","resolved":False,
                             "detail":"Source meaning must be retained.",
                             "source_anchor":{"quote":"Source token %02d."%n,"occurrence":0},
                             "translation_anchor":ta,"resolves":None})
            patches.append({"finding_id":fid,"operation":"replace","target_anchor":ta,
                            "replacement":"New token %02d."%n})
        record={"record_id":"b"*32,"source_sha256":quality.digest(self.source),
                "translation_sha256":quality.digest(self.old),"quality_report":{"findings":findings}}
        self.stage.write_text(json.dumps({"patches":patches,"glossary":[],"style_guide":[]}),encoding="utf-8")
        return record,patches
    def verify(self,record,patches):
        return runner.verify_transactional_candidate("d"*32,"synthetic",self.status,self.root,
                           self.chapter,record,self.source,self.old,patches,self.stage)
    def reload_status(self):
        self.status=json.loads(json.dumps(self.status,ensure_ascii=False))
    def assert_unchanged(self):
        self.assertEqual(self.path.read_bytes(),self.old.encode("utf-8"))
    def test_multi_group_restarts_only_pay_for_unchecked_cases(self):
        record,patches=self.fixture(range(1,10))
        first=["block-%06d"%n for n in range(1,9)]
        last=["block-000009"]
        with patch.object(runner,"complete_for_job",side_effect=[verdict(first),verdict(last)]) as model:
            self.assertFalse(self.verify(record,patches))
            self.assert_unchanged()
            self.reload_status()
            self.assertTrue(self.verify(record,patches))
            self.reload_status()
            self.assertTrue(self.verify(record,patches))
            self.assertEqual(model.call_count,2)
            self.assertTrue(all(x.args[1]=="REVIEWER" for x in model.call_args_list))
        self.assertEqual(self.status["model_calls_started"],2)
        self.assert_unchanged()
    def test_veto_removes_only_bad_staged_edit_then_reuses_exact_safe_check(self):
        record,patches=self.fixture([2,18])
        with patch.object(runner,"complete_for_job",return_value=verdict(
                ["block-000002","block-000018"],["block-000018"])) as model:
            with self.assertRaisesRegex(runner.ModelError,"vetoed"):
                self.verify(record,patches)
            self.assert_unchanged()
            remaining=json.loads(self.stage.read_text("utf-8"))["patches"]
            self.assertEqual(remaining,[patches[0]])
            self.reload_status()
            self.assertTrue(self.verify(record,remaining))
            self.assertEqual(model.call_count,1)
        self.assertEqual(self.status["model_calls_started"],1)
        self.assert_unchanged()
    def test_changed_memory_invalidates_approval(self):
        record,patches=self.fixture([2])
        with patch.object(runner,"complete_for_job",return_value=verdict(["block-000002"])) as model:
            self.assertTrue(self.verify(record,patches))
            self.reload_status()
            (self.book/"style-guide.md").write_text("Preserve narrative context.",encoding="utf-8")
            self.assertTrue(self.verify(record,patches))
            self.assertEqual(model.call_count,2)
        self.assert_unchanged()
    def test_invalid_verdict_cannot_create_approval(self):
        record,patches=self.fixture([2])
        with patch.object(runner,"complete_for_job",side_effect=["{}",verdict(["block-000002"])]) as model:
            with self.assertRaisesRegex(runner.ModelError,"invalid evidence"):
                self.verify(record,patches)
            self.assertEqual(self.status["transactional_case_approvals"]["approved"],[])
            self.reload_status()
            self.assertTrue(self.verify(record,patches))
            self.assertEqual(model.call_count,2)
        self.assert_unchanged()

    def test_veto_feedback_is_not_a_protocol_failure_and_only_failing_case_retried(self):
        record,patches=self.fixture([2,18])
        def reply(ids, reject=()):
            return json.dumps({"schema_version":2,"checks":dict.fromkeys(candidate_review.CHECKS,True),
                "decisions":[{"finding_id":key,"verdict":"reject" if key in reject else "approve",
                    "risk":"meaning" if key in reject else "none",
                    "reason":"Preserve the implied action." if key in reject else "The corrected meaning is supported."}
                    for key in ids]})
        import correction_feedback as feedback
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_CORRECTION_FEEDBACK":"on"}):
            with patch.object(runner,"complete_for_job",side_effect=[
                    reply(["block-000002","block-000018"],["block-000018"]),
                    reply(["block-000018"],["block-000018"])]) as model:
                self.assertFalse(self.verify(record,patches))
                self.assertUnchangedStage= json.loads(self.stage.read_text("utf-8"))["patches"]
                self.assertEqual(self.assertUnchangedStage,[patches[0]])
                self.assertEqual(self.status["phase"],"candidate-veto-feedback-staged")
                self.assertEqual(self.status.get("failures",0),0)
                self.reload_status()
                items=feedback.state(self.status,self.chapter,record)
                self.assertIn("Preserve the implied action",feedback.prompt_feedback(items,record["quality_report"]["findings"]))
                second=[patches[0],{**patches[1],"replacement":"Another token 18."}]
                self.stage.write_text(json.dumps({"patches":second,"glossary":[],"style_guide":[]}),encoding="utf-8")
                self.assertFalse(self.verify(record,second))
                self.assertEqual(feedback.exhausted(feedback.state(self.status,self.chapter,record)),{"fix-18"})
                remaining=json.loads(self.stage.read_text("utf-8"))["patches"]
                self.assertEqual(remaining,[patches[0]])
                self.assertTrue(self.verify(record,remaining))
                self.assertEqual(model.call_count,2)
                self.assertEqual(self.status.get("failures",0),0)
                self.assert_unchanged()

if __name__=="__main__":
    unittest.main()
