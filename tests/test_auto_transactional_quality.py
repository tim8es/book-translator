import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import transactional_quality as q
import correction_regression
import candidate_review
import book_runner

def marked(*paragraphs):
    return "".join("<!-- bt-block:%06d -->\n%s\n\n"%(i+1,p) for i,p in enumerate(paragraphs))

def finding(fid,source_quote,target_quote,category="terminology"):
    return {"id":fid,"category":category,"severity":"minor","detail":"Correct the name without losing context",
            "resolved":False,"source_anchor":{"quote":source_quote,"occurrence":0},
            "translation_anchor":{"quote":target_quote,"occurrence":0},"resolves":None}

def edit(f,text):
    return {"finding_id":f["id"],"operation":"replace","target_anchor":f["translation_anchor"],"replacement":text}

class TransactionalQualityTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY":"on"})
        env.start();self.addCleanup(env.stop)
        self.source=marked("Bluejay arrived at the station carrying the letter that Mira had left in the office.",
                           "Anton watched the gate.")
        self.old=marked("Блюджей прибыл на станцию с письмом, которое Мира оставила в конторе.",
                        "Антон следил за воротами.")
        self.f=finding("name","Bluejay arrived at the station carrying the letter that Mira had left in the office.",
                       "Блюджей прибыл на станцию с письмом, которое Мира оставила в конторе.")
    def safe(self,patches,findings):
        after=book_runner.apply_correction_patches(self.old,patches)
        q.require_safe(self.source,self.old,after,patches,findings,book_runner._anchor_position)
        return after
    def test_catastrophic_loss_rejected(self):
        with self.assertRaisesRegex(correction_regression.CorrectionRegression,"suspicious-anchor-content-loss"):
            self.safe([edit(self.f,"Синяя Сойка")],[self.f])
    def test_empty_non_addition_rejected(self):
        with self.assertRaisesRegex(correction_regression.CorrectionRegression,"empty-non-addition"):
            self.safe([edit(self.f,"")],[self.f])
    def test_wrong_source_block_rejected(self):
        f=finding("wrong","Anton watched the gate.",self.f["translation_anchor"]["quote"])
        with self.assertRaisesRegex(correction_regression.CorrectionRegression,"correction-block-scope-mismatch"):
            self.safe([edit(f,self.f["translation_anchor"]["quote"].replace("Блюджей","Синяя Сойка"))],[f])
    def test_correct_full_replacement(self):
        p=edit(self.f,self.f["translation_anchor"]["quote"].replace("Блюджей","Синяя Сойка"))
        after=self.safe([p],[self.f])
        cases=q.transaction_cases(self.source,self.old,after,[p],[self.f],book_runner._anchor_position)
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]["patch_ids"],["name"])
        self.assertIn("письмом",cases[0]["candidate_translation_excerpt"]["block"])
        groups=q.group_prompts(cases,"Independent review contract")
        self.assertEqual(len(groups),1)
        self.assertIn("COMBINED",groups[0][2])
    def test_multiple_edits_same_block(self):
        f=finding("name","Bluejay","Блюджей")
        g=finding("office","office","конторе")
        ps=[edit(f,"Синяя Сойка"),edit(g,"офисе")]
        after=self.safe(ps,[f,g])
        cases=q.transaction_cases(self.source,self.old,after,ps,[f,g],book_runner._anchor_position)
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]["patch_ids"],["name","office"])
        self.assertIn("Синяя Сойка",cases[0]["candidate_translation_excerpt"]["block"])
        self.assertIn("офисе",cases[0]["candidate_translation_excerpt"]["block"])
    def test_context_and_off_switch(self):
        c=q.correction_context(self.source,self.old,self.f,book_runner._anchor_position)
        self.assertIn("letter",c["complete_source_block"])
        self.assertIn("письмом",c["complete_current_target_block"])
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY":"off"}):
            self.safe([edit(self.f,"Синяя Сойка")],[self.f])
    def test_unaligned_large_fail_closed(self):
        with self.assertRaisesRegex(candidate_review.CandidateReviewError,"unaligned transaction"):
            q.transaction_cases("s"*10000,"t"*10000,"u"*10000,[edit(self.f,"Новый текст")],[self.f],book_runner._anchor_position)

if __name__=="__main__":
    unittest.main()
