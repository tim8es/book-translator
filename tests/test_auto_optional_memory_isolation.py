"""Malformed optional model proposals cannot erase sound core evidence."""
import json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
from literary_context import normalize_optional_memory, parse_translation
from omniroute import ModelError

class OptionalMemoryIsolationTests(unittest.TestCase):
    def test_mixed_proposals_preserve_core_without_repair_call(self):
        valid={"source_pattern":"short dialogue","translation_approach":"keep it short","evidence":"a brief greeting"}
        raw={"translation":"Текст.","glossary":[{"original":"Station","translation":"Станция","type":"место","notes":""}],
             "style_guide":[{"invalid_key":"wrong"},valid,{"source_pattern":"x","translation_approach":"y","evidence":"a\nb"}]}
        text=json.dumps(raw,ensure_ascii=False)
        with self.assertRaises(ModelError):
            parse_translation(text)
        normalized,rejected=normalize_optional_memory(text)
        self.assertEqual(rejected,2)
        translated,glossary,style=parse_translation(normalized)
        self.assertEqual(translated,"Текст.\n")
        self.assertEqual(style,[valid]);self.assertEqual(glossary,raw["glossary"])

    def test_preserves_every_core_field_and_does_not_repair_unknown_envelope(self):
        for core in ({"segments":[{"id":"s00001","translation":"text"}]},
                     {"patches":[{"finding_id":"f1","operation":"replace","replacement":"x"}]},
                     {"outcome":"CORRECTIONS_REQUIRED","checks":{"meaning":False},"findings":[{"id":"bad"}]}):
            raw={**core,"glossary":{},"style_guide":[{"bad":"x"}]}
            normalized,count=normalize_optional_memory(json.dumps(raw))
            self.assertEqual(count,2)
            self.assertEqual(json.loads(normalized),{**core,"glossary":[],"style_guide":[]})
        unknown=json.dumps({"outcome":"PASS","glossary":{},"style_guide":[]})
        self.assertEqual(normalize_optional_memory(unknown),(unknown,0))
        self.assertEqual(normalize_optional_memory("not JSON"),("not JSON",0))

if __name__=="__main__":unittest.main()
