import copy
import hashlib
import importlib
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.reviews import ReviewResolution
from workflow_v2.schemas import SchemaKind, parse_document

try:
    epub_output = importlib.import_module("workflow_v2.epub_output")
except ModuleNotFoundError:
    epub_output = None


REVISION = "0123456789abcdef"
CONTRACT = f"docs/TRANSLATION.md@{REVISION}"
SOURCE_HASH = "1" * 64
TRANSLATION_HASH = "2" * 64


class WorkflowV2EpubOutputIdentityTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "schema_version": 1,
            "title": "Example Book",
            "author": "Example Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "sample.md",
            "chapter_count": 1,
            "imported_at": "2026-09-06T00:00:00+00:00",
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "sample",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001-one.md",
                    "translation_path": "translated/001-one.md",
                    "status": "reviewed",
                }
            ],
        }
        self.artifacts = {
            "extracted/001-one.md": b"# One\n\nAlpha.\n",
            "translated/001-one.md": "# Один\n\nАльфа.\n".encode("utf-8"),
        }

    def api(self, name):
        self.assertIsNotNone(epub_output, "workflow_v2.epub_output is not implemented")
        value = getattr(epub_output, name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def reader(self, path):
        return self.artifacts[path]

    def pass_resolution(self, *, record_id="a" * 32, review_commit="review-a"):
        return ReviewResolution(
            unit_id="chapter-000001",
            chapter_number=1,
            state="pass",
            source_sha256=SOURCE_HASH,
            translation_sha256=TRANSLATION_HASH,
            current_record={
                "record_id": record_id,
                "workflow_revision": REVISION,
                "review_contract_revision": CONTRACT,
                "review_commit": review_commit,
                "outcome": "PASS",
            },
            history=(),
        )

    def snapshot(self, *, metadata=None, progress=None, resolutions=None, preview=False):
        build_input_snapshot = self.api("build_input_snapshot")
        return build_input_snapshot(
            metadata or self.metadata,
            progress or self.progress,
            resolutions if resolutions is not None else [self.pass_resolution()],
            self.reader,
            preview=preview,
        )

    def fingerprint(self, snapshot):
        return self.api("input_fingerprint")(snapshot)

    def test_final_fingerprint_ignores_duplicate_review_record_identity(self):
        first = self.snapshot(resolutions=[self.pass_resolution(record_id="a" * 32, review_commit="commit-a")])
        second = self.snapshot(resolutions=[self.pass_resolution(record_id="b" * 32, review_commit="commit-b")])
        self.assertEqual(self.fingerprint(first), self.fingerprint(second))

    def test_relevant_translation_metadata_order_cover_and_review_changes_change_fingerprint(self):
        baseline = self.fingerprint(self.snapshot())

        self.artifacts["translated/001-one.md"] = "# Один\n\nИзменено.\n".encode("utf-8")
        changed_translation = self.fingerprint(self.snapshot())
        self.assertNotEqual(changed_translation, baseline)
        self.artifacts["translated/001-one.md"] = "# Один\n\nАльфа.\n".encode("utf-8")

        metadata = copy.deepcopy(self.metadata)
        metadata["title"] = "Changed title"
        self.assertNotEqual(self.fingerprint(self.snapshot(metadata=metadata)), baseline)

        progress = copy.deepcopy(self.progress)
        progress["chapters"][0]["title"] = "Changed chapter title"
        self.assertNotEqual(self.fingerprint(self.snapshot(progress=progress)), baseline)

        stale = self.pass_resolution()
        stale = ReviewResolution(
            unit_id=stale.unit_id,
            chapter_number=stale.chapter_number,
            state="stale",
            source_sha256=stale.source_sha256,
            translation_sha256=stale.translation_sha256,
            current_record=stale.current_record,
            history=(),
        )
        self.assertNotEqual(self.fingerprint(self.snapshot(resolutions=[stale])), baseline)

        metadata = copy.deepcopy(self.metadata)
        metadata["cover_path"] = "cover.png"
        self.artifacts["cover.png"] = b"PNG-CONTENT"
        with_cover = self.api("build_input_snapshot")(
            metadata,
            self.progress,
            [self.pass_resolution()],
            self.reader,
            preview=False,
            cover_reader=self.reader,
        )
        self.assertNotEqual(self.fingerprint(with_cover), baseline)

    def test_preview_fingerprint_does_not_require_review_identity(self):
        progress = copy.deepcopy(self.progress)
        progress["chapters"][0]["status"] = "translated"
        snapshot = self.snapshot(progress=progress, resolutions=[], preview=True)
        self.assertEqual(snapshot["preview"], True)
        self.assertEqual(snapshot["units"][0]["status"], "translated")
        self.assertNotIn("review", snapshot["units"][0])

    def test_fingerprint_is_canonical_json_sha256(self):
        snapshot = self.snapshot()
        expected = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(self.fingerprint(snapshot), expected)


class WorkflowV2OutputManifestTests(unittest.TestCase):
    def api(self, name):
        self.assertIsNotNone(epub_output, "workflow_v2.epub_output is not implemented")
        value = getattr(epub_output, name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def manifest(self, *, fingerprint="3" * 64, artifact_hash="4" * 64):
        return {
            "schema_version": 1,
            "build_contract": "epub-build-v1",
            "book_slug": "sample",
            "format": "epub",
            "preview": False,
            "artifact_path": "output/sample.epub",
            "artifact_sha256": artifact_hash,
            "unit_count": 1,
            "input_fingerprint": fingerprint,
            "repository_commit": None,
            "state_revisions": {
                "metadata": "metadata-rev",
                "progress": "progress-rev",
                "review_ledger": "ledger-rev",
            },
        }

    def test_output_manifest_schema_kind_accepts_contract_and_rejects_unsafe_path(self):
        kind = getattr(SchemaKind, "OUTPUT_MANIFEST", None)
        self.assertIsNotNone(kind, "SchemaKind.OUTPUT_MANIFEST is not implemented")
        parsed = parse_document(kind, self.manifest())
        self.assertEqual(parsed.data["artifact_path"], "output/sample.epub")

        invalid = self.manifest()
        invalid["artifact_path"] = "../sample.epub"
        with self.assertRaises(Exception):
            parse_document(kind, invalid)

    def test_manifest_builder_emits_valid_schema(self):
        kind = getattr(SchemaKind, "OUTPUT_MANIFEST", None)
        self.assertIsNotNone(kind, "SchemaKind.OUTPUT_MANIFEST is not implemented")
        build_manifest = self.api("build_output_manifest")
        manifest = build_manifest(
            book_slug="sample",
            preview=False,
            artifact_path="output/sample.epub",
            artifact_sha256="4" * 64,
            unit_count=1,
            input_fingerprint="3" * 64,
            repository_commit=None,
            state_revisions={
                "metadata": "metadata-rev",
                "progress": "progress-rev",
                "review_ledger": "ledger-rev",
            },
        )
        self.assertEqual(parse_document(kind, manifest).data, manifest)

    def test_output_status_distinguishes_missing_current_stale_and_invalid_hash(self):
        resolve_status = self.api("resolve_output_status")
        artifact = b"epub-bytes"
        artifact_hash = hashlib.sha256(artifact).hexdigest()
        fingerprint = "3" * 64
        manifest = self.manifest(fingerprint=fingerprint, artifact_hash=artifact_hash)

        self.assertEqual(
            resolve_status(None, artifact_bytes=None, current_fingerprint=fingerprint, expected_unit_count=1)["state"],
            "missing",
        )
        self.assertEqual(
            resolve_status(manifest, artifact_bytes=artifact, current_fingerprint=fingerprint, expected_unit_count=1)["state"],
            "current",
        )
        self.assertEqual(
            resolve_status(manifest, artifact_bytes=artifact, current_fingerprint="5" * 64, expected_unit_count=1)["state"],
            "stale",
        )
        self.assertEqual(
            resolve_status(manifest, artifact_bytes=b"tampered", current_fingerprint=fingerprint, expected_unit_count=1)["state"],
            "invalid",
        )


if __name__ == "__main__":
    unittest.main()
