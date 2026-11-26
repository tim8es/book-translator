"""A narrow delta must never inherit coverage after semantic corrections."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import book_runner as runner


class SemanticRereviewTests(unittest.TestCase):
    def test_only_minor_editorial_findings_can_use_delta(self):
        source, target = "The envelope had no money.", "В конверте не было денег."
        sha = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()
        chapter = {"number": 1, "unit_id": "chapter-000001"}
        delta = {
            "advisory_only": True, "unit_id": chapter["unit_id"],
            "source_sha256": sha(source), "translation_sha256": sha(target),
            "mode": "focused_candidate", "previous_record_id": "prior",
            "sections": [], "reason": "test",
        }
        for category, severity, allowed in (
            ("meaning", "major", False), ("meaning", "minor", False),
            ("addition", "minor", False), ("omission", "major", False),
            ("modality", "major", False), ("referent", "minor", False),
            ("structure", "minor", False), ("style", "major", False),
            ("register", "minor", True), ("voice", "minor", True),
            ("style", "minor", True),
        ):
            with self.subTest(category=category, severity=severity):
                finding = {"category": category, "severity": severity,
                           "resolved": False, "source_anchor": {}}
                previous = {"record_id": "prior", "outcome": "CORRECTIONS_REQUIRED",
                            "source_sha256": sha(source), "translation_sha256": sha(target),
                            "quality_report": {"evidence_version": 2,
                                               "findings": [finding], "checks": {}}}
                with patch.object(runner, "cli", return_value=json.dumps(delta)), \
                     patch.object(runner, "_anchor_excerpt", return_value=source):
                    context = runner.focused_review_context("fixture", chapter, previous,
                                                            source, target)
                self.assertEqual(context is not None, allowed)
