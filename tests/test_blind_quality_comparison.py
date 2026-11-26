"""No paid calls; exercise the order-balanced benchmark protocol."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import blind_quality_comparison as bench


def artifact(text, source=None):
    result = {"text": text, "sha256": bench.digest(text)}
    if source is not None:
        result["source_sha256"] = source
    return result


def manifest():
    source = artifact("The box had seven coins.")
    return {"schema_version": 1, "target_language": "ru", "units": [
        {"unit_id": "chapter-000001", "source": source,
         "auto": artifact("В коробке семь монет.", source["sha256"]),
         "chatops": artifact("В коробке девять монет.", source["sha256"])}
    ]}


class BlindComparisonTests(unittest.TestCase):
    def test_swaps_labels_without_disclosing_origin_and_unblinds(self):
        inp = manifest()
        calls = []
        def judge(system, user):
            payload = json.loads(user)
            self.assertEqual(set(payload), {"source", "target_language", "A", "B"})
            self.assertNotIn("chatops", system.lower())
            self.assertNotIn("auto", system.lower())
            calls.append(payload)
            return json.dumps({key: {c: "семь" in payload[key] for c in bench.CRITERIA}
                               for key in ("A", "B")})
        report = {}
        bench.compare(inp, judge, report)
        self.assertEqual(calls[0]["A"], calls[1]["B"])
        self.assertEqual(calls[0]["B"], calls[1]["A"])
        self.assertEqual(report["calls_started"], 2)
        self.assertEqual(report["summary"]["auto"]["pass"], len(bench.CRITERIA))
        self.assertEqual(report["summary"]["chatops"]["fail"], len(bench.CRITERIA))
        self.assertNotIn(inp["units"][0]["auto"]["text"], json.dumps(report, ensure_ascii=False))

    def test_position_bias_is_disagreement_not_pass(self):
        report = {}
        bench.compare(manifest(), lambda *_: json.dumps({
            "A": dict.fromkeys(bench.CRITERIA, True),
            "B": dict.fromkeys(bench.CRITERIA, False)}), report)
        for mode in bench.MODES:
            self.assertEqual(report["summary"][mode]["disagreement"], len(bench.CRITERIA))
            self.assertEqual(report["summary"][mode]["pass"], 0)

    def test_rejects_wrong_source_or_changed_target_before_call(self):
        for field, value in (("source_sha256", "0" * 64), ("text", "changed")):
            inp = copy.deepcopy(manifest())
            inp["units"][0]["chatops"][field] = value
            with self.assertRaises(ValueError):
                bench.compare(inp, lambda *_: self.fail("unexpected call"), {})

    def test_invalid_judge_protocol_stops_with_incomplete_report(self):
        report = {}
        with self.assertRaises(ValueError):
            bench.compare(manifest(), lambda *_: '{"A": {}, "B": {}}', report)
        self.assertEqual(report["state"], "incomplete")
        self.assertEqual(report["calls_started"], 1)
