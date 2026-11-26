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


class AnchoredBlindComparisonTests(unittest.TestCase):
    def test_anchored_negative_evidence_remains_blind_and_bound_to_order(self):
        calls = []
        def judge(system, user):
            payload = json.loads(user)
            self.assertNotIn("chatops", system.lower())
            self.assertNotIn("auto", system.lower())
            calls.append(payload)
            answer = {}
            for label in ("A", "B"):
                good = "семь" in payload[label]
                checks = dict.fromkeys(bench.CRITERIA, True)
                checks["fidelity"] = good
                evidence = {} if good else {"fidelity": {
                    "kind": "defect", "source_quote": "seven coins",
                    "target_quote": "девять монет", "detail": "Wrong quantity"}}
                answer[label] = {"checks": checks, "negative_evidence": evidence}
            return json.dumps(answer)
        report = {}
        bench.compare(manifest(), judge, report, evidence_redactor=lambda s: s)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["judge_protocol"], "anchored-negative-evidence-v1")
        self.assertEqual(report["summary"]["chatops"]["fail"], 1)
        for e in report["units"][0]["negative_evidence"]:
            self.assertEqual(e["auto"], {})
            self.assertEqual(e["chatops"]["fidelity"]["target_quote"], "девять монет")

    def test_invalid_or_missing_negative_evidence_is_not_pass(self):
        payload = {"source": "Seven coins.", "A": "Семь монет.", "B": "Девять монет."}
        answer = {label: {"checks": dict.fromkeys(bench.CRITERIA, True),
                          "negative_evidence": {}} for label in ("A", "B")}
        answer["B"]["checks"]["fidelity"] = False
        with self.assertRaises(ValueError):
            bench.parse_anchored_judgment(json.dumps(answer), payload)
        item = {"kind": "defect", "source_quote": "Seven coins",
                "target_quote": "Семь монет", "detail": "Wrong number"}
        answer["B"]["negative_evidence"]["fidelity"] = item
        with self.assertRaises(ValueError):
            bench.parse_anchored_judgment(json.dumps(answer), payload)
        item["target_quote"] = "Девять монет"
        checks, evidence = bench.parse_anchored_judgment(json.dumps(answer), payload)
        self.assertFalse(checks["B"]["fidelity"])
        self.assertEqual(evidence["B"]["fidelity"]["kind"], "defect")
        item.update(kind="uncertainty", target_quote="", detail="Cannot verify")
        self.assertFalse(bench.parse_anchored_judgment(json.dumps(answer), payload)[0]["B"]["fidelity"])

    def test_protocol_failure_does_not_retry_or_award_pass(self):
        report = {}
        with self.assertRaises(ValueError):
            bench.compare(manifest(), lambda *_: '{"A": {}, "B": {}}', report,
                          evidence_redactor=lambda s: s)
        self.assertEqual(report["state"], "incomplete")
        self.assertEqual(report["calls_started"], 1)
