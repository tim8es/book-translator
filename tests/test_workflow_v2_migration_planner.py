import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2 import FilesystemStorage, WorkflowStateRepository

try:
    from workflow_v2.migrations import MigrationCompatibilityError, MigrationPlanner
except ImportError:
    MigrationCompatibilityError = None
    MigrationPlanner = None


CANONICAL = "https://github.com/tim8es/book-translator"
OLD = "old-revision"
NEW = "new-revision"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class WorkflowV2MigrationPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.book_dir = Path(self.temp.name) / "books" / "legacy"
        for name in ("source", "extracted", "translated", "output", ".workflow/claims"):
            (self.book_dir / name).mkdir(parents=True, exist_ok=True)
        (self.book_dir / "source" / "legacy.md").write_bytes(b"source-book\n")
        (self.book_dir / "extracted" / "001-one.md").write_bytes(b"source chapter\n")
        (self.book_dir / "translated" / "001-one.md").write_bytes("перевод\n".encode("utf-8"))
        self.storage = FilesystemStorage(self.book_dir)
        self.repository = WorkflowStateRepository(self.storage)

    def tearDown(self):
        self.temp.cleanup()

    def require_api(self):
        self.assertIsNotNone(MigrationPlanner, "MigrationPlanner is not implemented")
        self.assertIsNotNone(MigrationCompatibilityError)

    @staticmethod
    def installed(revision=NEW):
        return {
            "canonical_repository": CANONICAL,
            "requested_ref": "refactor/workflow-engine-v2",
            "resolved_revision": revision,
        }

    def metadata(self, *, revision=OLD, version=True, review_marker=False, private=False):
        data = {
            "schema_version": 1,
            "title": "Legacy",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "legacy.md",
            "chapter_count": 1,
            "imported_at": "2026-09-01T10:00:00+00:00",
            "workflow": {
                "repository": CANONICAL,
                "requested_ref": "legacy-ref",
                "resolved_revision": revision,
            },
        }
        if review_marker:
            data["workflow"]["review_evidence"] = "review-ledger-v1"
        if private:
            source_bytes = b"private-source-never-stored"
            data["source"] = {
                "storage_mode": "private_external",
                "filename": "legacy.md",
                "size_bytes": len(source_bytes),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            }
        if not version:
            data.pop("schema_version")
        return data

    def progress(self, *, status="reviewed", version=True):
        data = {
            "schema_version": 1,
            "book_slug": "legacy",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001-one.md",
                    "translation_path": "translated/001-one.md",
                    "status": status,
                }
            ],
        }
        if not version:
            data.pop("schema_version")
        return data

    def ledger(self, *, revision=NEW, with_pass=False, version=True):
        records = []
        if with_pass:
            source_sha = hashlib.sha256((self.book_dir / "extracted/001-one.md").read_bytes()).hexdigest()
            translation_sha = hashlib.sha256((self.book_dir / "translated/001-one.md").read_bytes()).hexdigest()
            records.append(
                {
                    "record_id": "a" * 32,
                    "sequence": 1,
                    "unit_id": "chapter-000001",
                    "outcome": "PASS",
                    "source_sha256": source_sha,
                    "translation_sha256": translation_sha,
                    "workflow_revision": revision,
                    "review_contract_revision": f"docs/TRANSLATION.md@{revision}",
                    "reviewer_session_id": "reviewer-1",
                    "reviewed_at": "2026-09-01T11:00:00Z",
                    "state_revision": "progress-old",
                    "review_commit": None,
                    "correction_round": 0,
                    "supersedes_record_id": None,
                }
            )
        data = {
            "schema_version": 1,
            "book_slug": "legacy",
            "next_sequence": len(records) + 1,
            "records": records,
        }
        if not version:
            data.pop("schema_version")
        return data

    def manifest(self, *, version=True):
        source = self.book_dir / "source" / "legacy.md"
        extracted = self.book_dir / "extracted" / "001-one.md"
        data = {
            "schema_version": 1,
            "source_file": "legacy.md",
            "source_format": "markdown",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "chapter_count": 1,
            "extracted": [
                {
                    "number": 1,
                    "title": "One",
                    "path": "extracted/001-one.md",
                    "sha256": hashlib.sha256(extracted.read_bytes()).hexdigest(),
                }
            ],
        }
        if not version:
            data.pop("schema_version")
        return data

    def write_json(self, path, data):
        target = self.book_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def artifact_reader(self, relative_path):
        return (self.book_dir / relative_path).read_bytes()

    def planner(self):
        self.require_api()
        return MigrationPlanner(
            self.repository,
            book_dir=self.book_dir,
            artifact_reader=self.artifact_reader,
            now=lambda: NOW,
        )

    def durable_snapshot(self):
        return {
            path.relative_to(self.book_dir).as_posix(): path.read_bytes()
            for path in sorted(self.book_dir.rglob("*"))
            if path.is_file()
        }

    def test_legacy_embedded_workspace_plans_manifest_ledger_review_downgrade_and_metadata_pin(self):
        self.write_json("metadata.json", self.metadata(version=False))
        self.write_json("progress.json", self.progress(version=False, status="reviewed"))
        before = self.durable_snapshot()

        plan = self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())

        self.assertTrue(plan.changed)
        self.assertEqual(plan.from_revision, OLD)
        self.assertEqual(plan.to_revision, NEW)
        self.assertEqual(plan.lifecycle_downgrades, (1,))
        self.assertEqual(self.durable_snapshot(), before, "planning must be read-only")

        metadata = plan.write_for("metadata.json")
        progress = plan.write_for("progress.json")
        ledger = plan.write_for("review-ledger.json")
        manifest = plan.write_for("source-manifest.json")
        self.assertIsNotNone(metadata)
        self.assertIsNotNone(progress)
        self.assertIsNotNone(ledger)
        self.assertIsNotNone(manifest)
        self.assertEqual(metadata.target_data["workflow"]["resolved_revision"], NEW)
        self.assertEqual(metadata.target_data["workflow"]["review_evidence"], "review-ledger-v1")
        self.assertEqual(progress.target_data["chapters"][0]["status"], "translated")
        self.assertEqual(ledger.target_data["records"], [])
        self.assertEqual(manifest.target_data["source_file"], "legacy.md")

    def test_target_must_equal_installed_revision_and_failure_is_read_only(self):
        self.write_json("metadata.json", self.metadata(version=False))
        self.write_json("progress.json", self.progress(version=False, status="translated"))
        before = self.durable_snapshot()
        with self.assertRaises(MigrationCompatibilityError) as ctx:
            self.planner().plan(slug="legacy", to_revision="other", installed=self.installed())
        self.assertIn("installed", str(ctx.exception).lower())
        self.assertEqual(self.durable_snapshot(), before)

    def test_current_pass_for_same_target_revision_preserves_reviewed_lifecycle(self):
        self.write_json("metadata.json", self.metadata(revision=NEW, version=False, review_marker=True))
        self.write_json("progress.json", self.progress(version=False, status="reviewed"))
        self.write_json("review-ledger.json", self.ledger(revision=NEW, with_pass=True, version=False))
        self.write_json("source-manifest.json", self.manifest(version=False))

        plan = self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertEqual(plan.lifecycle_downgrades, ())
        progress = plan.write_for("progress.json")
        self.assertIsNotNone(progress, "v0 progress still requires schema migration")
        self.assertEqual(progress.target_data["chapters"][0]["status"], "reviewed")

    def test_reviewed_unit_without_valid_translation_is_incompatible(self):
        self.write_json("metadata.json", self.metadata(version=False))
        self.write_json("progress.json", self.progress(version=False, status="reviewed"))
        (self.book_dir / "translated/001-one.md").write_bytes(b"")
        with self.assertRaises(MigrationCompatibilityError) as ctx:
            self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertIn("translation", str(ctx.exception).lower())

    def test_private_external_manifest_is_reconstructed_without_source_binary(self):
        (self.book_dir / "source/legacy.md").unlink()
        self.write_json("metadata.json", self.metadata(version=False, private=True))
        self.write_json("progress.json", self.progress(version=False, status="translated"))

        plan = self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        manifest = plan.write_for("source-manifest.json")
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.target_data["source_storage_mode"], "private_external")
        self.assertEqual(manifest.target_data["source_sha256"], self.metadata(private=True)["source"]["sha256"])
        self.assertFalse((self.book_dir / "source/legacy.md").exists())

    def test_unprovable_source_identity_fails_before_mutation(self):
        (self.book_dir / "source/legacy.md").unlink()
        self.write_json("metadata.json", self.metadata(version=False))
        self.write_json("progress.json", self.progress(version=False, status="translated"))
        before = self.durable_snapshot()
        with self.assertRaises(MigrationCompatibilityError) as ctx:
            self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertIn("source", str(ctx.exception).lower())
        self.assertEqual(self.durable_snapshot(), before)

    def test_live_claim_and_finalization_block_but_expired_claim_can_be_migrated_without_extension(self):
        self.write_json("metadata.json", self.metadata(version=False))
        self.write_json("progress.json", self.progress(version=False, status="translated"))
        claim = {
            "claim_id": "b" * 32,
            "unit_id": "chapter-000001",
            "role": "translator",
            "session_id": "worker",
            "base_revision": "base",
            "base_commit": None,
            "workflow_revision": OLD,
            "claimed_at": "2026-09-07T11:30:00Z",
            "expires_at": "2026-09-07T12:30:00Z",
        }
        self.write_json(".workflow/claims/chapter-000001.json", claim)
        with self.assertRaises(MigrationCompatibilityError) as live:
            self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertIn("claim", str(live.exception).lower())

        claim["claimed_at"] = "2026-09-07T10:00:00Z"
        claim["expires_at"] = "2026-09-07T11:00:00Z"
        self.write_json(".workflow/claims/chapter-000001.json", claim)
        plan = self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        migrated_claim = plan.write_for(".workflow/claims/chapter-000001.json")
        self.assertIsNotNone(migrated_claim)
        self.assertEqual(migrated_claim.target_data["expires_at"], "2026-09-07T11:00:00Z")

        (self.book_dir / ".workflow/claims/chapter-000001.json").unlink()
        self.write_json(".workflow/finalization.json", {"anything": "present"})
        with self.assertRaises(MigrationCompatibilityError) as finalizing:
            self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertIn("final", str(finalizing.exception).lower())

    def test_fully_current_workspace_is_true_noop(self):
        self.write_json("metadata.json", self.metadata(revision=NEW, review_marker=True))
        self.write_json("progress.json", self.progress(status="translated"))
        self.write_json("review-ledger.json", self.ledger(revision=NEW))
        self.write_json("source-manifest.json", self.manifest())
        before = self.durable_snapshot()

        plan = self.planner().plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertFalse(plan.changed)
        self.assertEqual(plan.writes, ())
        self.assertEqual(plan.lifecycle_downgrades, ())
        self.assertEqual(self.durable_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
