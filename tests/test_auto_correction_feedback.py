import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import correction_feedback as feedback
import candidate_review
import block_recovery
import book_runner
import execution_metrics

class FeedbackTests(unittest.TestCase):
    def test_complete_explained_schema_required(self):
        d={"schema_version":2,"checks":dict.fromkeys(candidate_review.CHECKS,True),
            "decisions":[{"finding_id":"x","verdict":"reject","risk":"no_progress","reason":"The old error remains."}]}
        self.assertEqual(feedback.decisions(json.dumps(d),["x"])["x"]["risk"],"no_progress")
        d["decisions"][0]["reason"]=""
        with self.assertRaises(candidate_review.CandidateReviewError):
            feedback.decisions(json.dumps(d),["x"])
    def test_plain_anchored_reconstruction_without_assumed_alignment(self):
        source="First.\n\nHe might be there.\n\nLast."
        target="Начало.\n\nОн точно там.\n\nКонец."
        finding={"id":"m","source_anchor":{"quote":"He might be there.","occurrence":0},
                 "translation_anchor":{"quote":"Он точно там.","occurrence":0}}
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_CORRECTION_FEEDBACK":"on"}):
            spec=block_recovery.spec(source,target,finding,book_runner._anchor_position)
        self.assertIsNotNone(spec)
        self.assertIn("He might be there.",spec["source"])
        answer={"finding_id":"m","candidate_block":spec["prefix"]+"Возможно, он там."+spec["suffix"],"glossary":[],"style_guide":[]}
        edit=json.loads(block_recovery.compile_response(json.dumps(answer),spec))
        self.assertEqual(edit["patches"][0]["replacement"],"Возможно, он там.")
    def test_metrics_distinguish_independent_check_from_corrector_claim(self):
        s={}
        execution_metrics.provider(s,{"role":"corrector","requested_role":"reviewer","outcome":"response","duration_ms":100})
        self.assertEqual(s["execution_metrics"]["api"]["corrector"]["completed_calls"],1)
        self.assertEqual(s["execution_metrics"]["requested_api"]["reviewer"]["completed_calls"],1)

if __name__=="__main__":
    unittest.main()
