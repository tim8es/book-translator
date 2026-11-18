import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import plain_review_scope as plain
import compact_review as compact
import transactional_quality as quality
import review_chunks
import book_runner

def corpus(prefix,n=120):
    return "\n\n".join("%s %03d. "%(prefix,i)+("A distinct sentence about a train, its journey and the station. "*4)
                       for i in range(n))+"\n"

def finding(fid,s,t):
    return {"id":fid,"category":"meaning","severity":"minor","resolved":False,"detail":"Restore original meaning.",
            "source_anchor":{"quote":s,"occurrence":0},
            "translation_anchor":{"quote":t,"occurrence":0},"resolves":None}

def edit(f,text):
    return {"finding_id":f["id"],"operation":"replace","target_anchor":f["translation_anchor"],"replacement":text}

class PlainCorpusTests(unittest.TestCase):
    def test_marker_free_large_initial_review_and_complete_packet_core(self):
        source,target=corpus("Source"),corpus("Target",125)
        self.assertGreater(len(source),26000)
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_COMPACT_REVIEW":"on"}):
            self.assertTrue(compact.prefer_initial_chunks(source,target))
        plan=review_chunks.plan_aligned(source,target)
        for chunk in plan["chunks"]:
            packet=compact.packet(source,target,chunk)
            self.assertIn("source_paragraphs",packet)
            self.assertNotIn("pairs",packet)
            self.assertEqual("".join(x["text"] for x in packet["source_paragraphs"]),
                             source[slice(*chunk["source_core"])])
            self.assertEqual("".join(x["text"] for x in packet["target_paragraphs"]),
                             target[slice(*chunk["translation_core"])])
        chunk=plan["chunks"][0]
        data=compact.packet(source,target,chunk)
        item={"source_block":data["source_paragraphs"][0]["block_id"],"source_quote":"Source 000.",
              "target_block":data["target_paragraphs"][0]["block_id"],"target_quote":"Target 000.",
              "category":"meaning","severity":"minor","detail":"Exact discrepancy","prior":None,"resolved":False}
        answer=json.dumps({"outcome":"CORRECTIONS_REQUIRED","checks":dict.fromkeys(compact.CHECKS,True),
                           "findings":[item],"style_guide":[],"glossary":[]})
        result=json.loads(compact.compile_response(answer,source,target,chunk,[]))
        self.assertEqual(result["findings"][0]["source_anchor"]["quote"],"Source 000.")
        review_chunks.validate_chunk_report(chunk,result,source,target)
    def test_large_unmarked_candidate_checks_actual_combined_target_and_exact_sources(self):
        source,target=corpus("Source"),corpus("Target",125)
        fs=[finding("one","Source 001.","Target 001."),finding("two","Source 019.","Target 019.")]
        ps=[edit(fs[0],"Changed 001."),edit(fs[1],"Changed 019.")]
        candidate=book_runner.apply_correction_patches(target,ps)
        self.assertGreater(len(source)+len(target)+len(candidate),18000)
        cases=quality.transaction_cases(source,target,candidate,ps,fs,book_runner._anchor_position)
        self.assertEqual(len(cases),2)
        self.assertEqual([c["patch_ids"] for c in cases],[["one"],["two"]])
        self.assertIn("Changed 019.",cases[1]["candidate_translation_excerpt"]["block"])
        self.assertIn("Source 019.",cases[1]["original_excerpt"]["anchor_linked_passages"][0]["source"]["block"])
        self.assertTrue(quality.group_prompts(cases,"Pinned independent review"))
    def test_same_paragraph_combined_and_shifted_offsets(self):
        source="X alpha. Y beta.\n\nOther source.\n"
        target="Old alpha. Old beta.\n\nOther target.\n"
        fs=[finding("a","X alpha.","Old alpha."),finding("b","Y beta.","Old beta.")]
        ps=[edit(fs[0],"Correct alpha with more words."),edit(fs[1],"Fixed beta.")]
        after=book_runner.apply_correction_patches(target,ps)
        cases=quality.transaction_cases(source,target,after,ps,fs,book_runner._anchor_position)
        self.assertEqual(len(cases),1)
        self.assertIn("Correct alpha with more words. Fixed beta.",cases[0]["candidate_translation_excerpt"]["block"])
        with self.assertRaisesRegex(Exception,"exact staged edits"):
            quality.transaction_cases(source,target,after+"unapproved",ps,fs,book_runner._anchor_position)
    def test_unmarked_insertion_retains_exact_candidate(self):
        source="Original alpha. Missing detail.\n\nSecond."
        target="Old alpha.\n\nSecond."
        f=finding("a","Missing detail.","Old alpha.")
        p=edit(f," Added detail.")
        p["operation"]="insert_after"
        after=book_runner.apply_correction_patches(target,[p])
        case=quality.transaction_cases(source,target,after,[p],[f],book_runner._anchor_position)[0]
        self.assertIn("Old alpha. Added detail.",case["candidate_translation_excerpt"]["block"])

if __name__=="__main__":
    unittest.main()
