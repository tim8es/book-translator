"""Private structural EPUB reads exact original externally without copying it."""
from __future__ import annotations

import hashlib
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_cli import EpubCliError, _read_structural_original


class PrivateStructuralSourceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.book = self.root / "book"
        self.book.mkdir()
        self.original = self.root / "outside.epub"
        self.original.write_bytes(b"synthetic immutable EPUB binary")
        self.digest = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.metadata = {
            "source_file": "outside.epub",
            "source": {"sha256": self.digest, "size_bytes": self.original.stat().st_size,
                       "storage_mode": "private_external"},
        }
        self.layout = {"source_sha256": self.digest}

    def test_exact_external_source_passes_without_copying(self):
        data = _read_structural_original(self.book, self.metadata,
                                         self.layout, str(self.original))
        self.assertEqual(data, self.original.read_bytes())
        self.assertFalse((self.book / "source").exists())

    def test_private_mode_without_original_is_explicitly_blocked(self):
        with self.assertRaisesRegex(EpubCliError, "requires --source-epub"):
            _read_structural_original(self.book, self.metadata, self.layout, None)

    def test_changed_external_original_is_rejected(self):
        self.original.write_bytes(b"altered source")
        with self.assertRaisesRegex(EpubCliError, "differs"):
            _read_structural_original(self.book, self.metadata,
                                      self.layout, str(self.original))

    def test_layout_sha_mismatch_is_rejected(self):
        bad = {"source_sha256": "0" * 64}
        with self.assertRaisesRegex(EpubCliError, "differs"):
            _read_structural_original(self.book, self.metadata,
                                      bad, str(self.original))

    def test_embedded_original_is_used_when_available(self):
        self.metadata["source"]["storage_mode"] = "embedded"
        target = self.book / "source" / "outside.epub"
        target.parent.mkdir()
        target.write_bytes(self.original.read_bytes())
        data = _read_structural_original(self.book, self.metadata, self.layout, None)
        self.assertEqual(data, self.original.read_bytes())

    def test_unsafe_source_filename_is_rejected(self):
        self.metadata["source_file"] = "../outside.epub"
        with self.assertRaisesRegex(EpubCliError, "invalid EPUB original filename"):
            _read_structural_original(self.book, self.metadata,
                                      self.layout, str(self.original))


if __name__ == "__main__":
    unittest.main()
