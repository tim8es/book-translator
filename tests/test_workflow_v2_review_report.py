import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.reviews import ReviewLedgerManager
from workflow_v2.schemas import SchemaKind

try:
    from workflow_v2.review_report import build_review_report_snapshot
except (ImportError, ModuleNotFoundError):
    build_review_report_snapshot = None


WORKFLOW_REVISION = "0123456789abcdef"
CONTRACT_REVISION = f"docs/TRANSLATION.md@{WORKFLOW_REVISION}"


def sha256(content):
    return hashlib.sha256(content).hexdigest()


class WorkflowV2ReviewReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        self.artifacts = {}
        chapters = []
        for number, title in enumerate(("Pass", "Corrections", "Missing", "Stale", "Untranslated"), start=1):
            source_path = f"extracted/{number:03d}.md"
            translation_path = f"translated/{number:03d}.md"
            self.artifacts[source_path] = f"Source {number}\n".encode("utf-8")
            if number != 5:
                self.artifacts[translation_path] = f"Translation {number}\n".encode("utf-8")
            chapters.append(
                {
                    "number": number,
                    "title": title,
                    "slug": title.lower(),
                    "source_path": source_path,
                    "translation_path": translation_path,
                    "status": "extracted" if number == 5 else "translated",
                }
            )

        self.metadata = {
            "schema_version": 1,
            "title": "Report Example",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 5,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": WORKFLOW_REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "report-example",
            "chapters": chapters,
        }
        self.repository.create("metadata.json", SchemaKind.METADATA, self.metadata)
        self.repository.create("progress.json", SchemaKind.PROGRESS, self.progress)

        record_1 = self.record(
            1,
            1,
            "1" * 32,
            "PASS",
            translation_sha=sha256(self.artifacts["translated/001.md"]),
            review_commit="commit-pass-1",
            supersedes=None,
            correction_round=0,
        )
        record_2 = self.record(
            2,
            1,
            "2" * 32,
            "PASS",
            translation_sha=sha256(self.artifacts["translated/001.md"]),
            review_commit="commit-pass-2",
            supersedes=record_1["record_id"],
            correction_round=0,
        )
        record_3 = self.record(
            3,
            2,
            "3" * 32,
            "CORRECTIONS_REQUIRED",
            translation_sha=sha256(self.artifacts["translated/002.md"]),
            review_commit="commit-corrections",
            supersedes=None,
            correction_round=1,
        )
        stale_translation = b"Old translation 4\n"
        record_4 = self.record(
            4,
            4,
            "4" * 32,
            "PASS",
            translation_sha=sha256(stale_translation),
            review_commit="commit-stale",
            supersedes=None,
            correction_round=0,
        )
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {
                "schema_version": 1,
                "book_slug": "report-example",
                "next_sequence": 5,
                "records": [record_1, record_2, record_3, record_4],
            },
        )
        self.manager = ReviewLedgerManager(
            self.repository,
            artifact_reader=self.read_artifact,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def record(
        self,
        sequence,
        chapter,
        record_id,
        outcome,
        *,
        translation_sha,
        review_commit,
        supersedes,
        correction_round,
    ):
        return {
            "record_id": record_id,
            "sequence": sequence,
            "unit_id": f"chapter-{chapter:06d}",
            "outcome": outcome,
            "source_sha256": sha256(self.artifacts[f"extracted/{chapter:03d}.md"]),
            "translation_sha256": translation_sha,
            "workflow_revision": WORKFLOW_REVISION,
            "review_contract_revision": CONTRACT_REVISION,
            "reviewer_session_id": f"reviewer-{chapter}",
            "reviewed_at": f"2026-09-06T0{sequence}:00:00Z",
            "state_revision": f"state-{sequence}",
            "review_commit": review_commit,
            "correction_round": correction_round,
            "supersedes_record_id": supersedes,
        }

    def read_artifact(self, path):
        try:
            return self.artifacts[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def snapshot(self):
        self.assertIsNotNone(
            build_review_report_snapshot,
            "workflow_v2.review_report build_review_report_snapshot is not implemented",
        )
        return build_review_report_snapshot(self.manager, self.progress, self.metadata)

    def test_snapshot_counts_current_states_and_pass_coverage(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["schema"], "review-report-v1")
        self.assertEqual(snapshot["book_slug"], "report-example")
        self.assertEqual(
            snapshot["summary"],
            {
                "total_units": 5,
                "pass": 1,
                "corrections_required": 1,
                "missing": 1,
                "stale": 1,
                "untranslated": 1,
                "pass_coverage": {"passed": 1, "total": 5, "percent": 20.0},
                "duplicate_records": 1,
            },
        )
        self.assertEqual(
            [unit["state"] for unit in snapshot["units"]],
            ["pass", "corrections_required", "missing", "stale", "untranslated"],
        )

    def test_current_unit_contains_hashes_review_revision_and_commit(self):
        snapshot = self.snapshot()
        first = snapshot["units"][0]
        self.assertEqual(first["unit_id"], "chapter-000001")
        self.assertEqual(first["source_sha256"], sha256(self.artifacts["extracted/001.md"]))
        self.assertEqual(first["translation_sha256"], sha256(self.artifacts["translated/001.md"]))
        self.assertEqual(first["current_review"]["outcome"], "PASS")
        self.assertEqual(first["current_review"]["workflow_revision"], WORKFLOW_REVISION)
        self.assertEqual(first["current_review"]["review_commit"], "commit-pass-2")
        self.assertEqual(first["current_review"]["record_id"], "2" * 32)

        stale = snapshot["units"][3]
        self.assertEqual(stale["state"], "stale")
        self.assertIsNone(stale["current_review"])
        self.assertEqual(stale["translation_sha256"], sha256(self.artifacts["translated/004.md"]))

    def test_history_marks_superseded_duplicate_and_stale_records_explicitly(self):
        snapshot = self.snapshot()
        first_history = snapshot["units"][0]["history"]
        self.assertEqual(len(first_history), 2)
        self.assertEqual(first_history[0]["classification"], "superseded")
        self.assertIsNone(first_history[0]["duplicate_of_record_id"])
        self.assertEqual(first_history[1]["classification"], "current")
        self.assertEqual(first_history[1]["duplicate_of_record_id"], "1" * 32)

        stale_history = snapshot["units"][3]["history"]
        self.assertEqual(len(stale_history), 1)
        self.assertEqual(stale_history[0]["classification"], "stale")
        self.assertIsNone(stale_history[0]["duplicate_of_record_id"])

    def test_missing_and_untranslated_units_remain_visible_without_history(self):
        snapshot = self.snapshot()
        missing = snapshot["units"][2]
        untranslated = snapshot["units"][4]
        self.assertEqual(missing["state"], "missing")
        self.assertEqual(missing["history"], [])
        self.assertEqual(untranslated["state"], "untranslated")
        self.assertIsNone(untranslated["translation_sha256"])
        self.assertEqual(untranslated["history"], [])


if __name__ == "__main__":
    unittest.main()
