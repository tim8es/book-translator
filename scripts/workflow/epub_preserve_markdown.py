"""Opt-in, source-bound Markdown exchange for XHTML-preserving EPUB previews.

These Markdown files are *marked text*, not arbitrary Markdown-to-HTML input.
Translators change only prose between BT block boundaries and may reposition
balanced protected inline markers. No workflow acceptance is inferred.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from io import BytesIO
from typing import Mapping

from .epub_preserve_build import EpubPreserveBuildError, preview_from_source
from .epub_source_map import EpubSourceMapError, inventory_epub
from .xhtml_template import DocumentTemplate, XhtmlTemplateError, inspect_xhtml

MANIFEST_NAME = "xhtml-translation-map.json"
FORMAT = "xhtml-preserve-markdown-v1"
BLOCK_START = "<!-- BT:BLOCK "
BLOCK_END = "<!-- BT:END -->"
DOC_START = "<!-- BT:DOC "
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class EpubMarkedMarkdownError(ValueError):
    """The source-bound Markdown artifact cannot be trusted for assembly."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _document_filename(order: int) -> str:
    return f"{order:04d}.md"


def _serialize_doc(template: DocumentTemplate) -> str:
    parts = [f"{DOC_START}{template.source_sha256} -->\n"]
    for block in template.blocks:
        text = block.text
        if any(marker in text for marker in (DOC_START, BLOCK_START, BLOCK_END)):
            raise EpubMarkedMarkdownError("source prose contains protected BT delimiter")
        parts.append(f"\n{BLOCK_START}{block.key} {block.source_sha256} -->\n")
        parts.append(text)
        parts.append(f"\n{BLOCK_END}\n")
    return "".join(parts)


def _parse_doc(template: DocumentTemplate, artifact: str) -> dict[str, str]:
    if not isinstance(artifact, str):
        raise EpubMarkedMarkdownError("Markdown document is not UTF-8 text")
    if "\r" in artifact:
        raise EpubMarkedMarkdownError("CR line endings are unsupported for protected block map")
    expected = f"{DOC_START}{template.source_sha256} -->\n"
    if not artifact.startswith(expected):
        raise EpubMarkedMarkdownError("Markdown document identity/header mismatch")
    offset = len(expected)
    result: dict[str, str] = {}
    for block in template.blocks:
        prefix = f"\n{BLOCK_START}{block.key} {block.source_sha256} -->\n"
        if not artifact.startswith(prefix, offset):
            raise EpubMarkedMarkdownError(f"missing or reordered Markdown block: {block.key}")
        offset += len(prefix)
        suffix = f"\n{BLOCK_END}\n"
        end = artifact.find(suffix, offset)
        if end < 0:
            raise EpubMarkedMarkdownError(f"unterminated Markdown block: {block.key}")
        value = artifact[offset:end]
        if not value.strip() or DOC_START in value or BLOCK_START in value or BLOCK_END in value:
            raise EpubMarkedMarkdownError(f"empty or malformed protected block: {block.key}")
        result[block.key] = value
        offset = end + len(suffix)
    if artifact[offset:].strip():
        raise EpubMarkedMarkdownError("unmapped text or additional Markdown blocks after final block")
    return result


def make_markdown_bundle(source: bytes) -> tuple[dict, dict[str, str]]:
    """Create a deterministic, text-free manifest and editable .md files."""
    try:
        inventory = inventory_epub(source)
    except EpubSourceMapError as exc:
        raise EpubMarkedMarkdownError(f"source EPUB rejected: {exc}") from exc
    documents: list[dict] = []
    artifacts: dict[str, str] = {}
    with zipfile.ZipFile(BytesIO(source)) as archive:
        for item in inventory["spine"]:
            template = inspect_xhtml(archive.read(item["path"]))
            filename = _document_filename(item["order"])
            artifacts[filename] = _serialize_doc(template)
            documents.append({
                "order": item["order"],
                "path": item["path"],
                "filename": filename,
                "xhtml_sha256": template.source_sha256,
                "blocks": [{"key": block.key, "sha256": block.source_sha256}
                           for block in template.blocks],
            })
    report = {
        "schema_version": 1, "format": FORMAT,
        "source_sha256": inventory["source_sha256"],
        "documents": documents,
    }
    return report, artifacts


def preview_from_markdown(
    original: bytes,
    manifest: Mapping,
    markdown_files: Mapping[str, str],
    *,
    language: str,
    title: str | None = None,
) -> bytes:
    """Build only a PREVIEW; forbid incomplete/forged source-map identities."""
    expected_map, _ = make_markdown_bundle(original)
    if manifest != expected_map:
        raise EpubMarkedMarkdownError("source-bound Markdown manifest differs from exact EPUB")
    required = {doc["filename"] for doc in expected_map["documents"]}
    if set(markdown_files) != required:
        raise EpubMarkedMarkdownError("missing or unexpected Markdown documents")
    translations: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(BytesIO(original)) as archive:
        for doc in expected_map["documents"]:
            template = inspect_xhtml(archive.read(doc["path"]))
            translations[doc["path"]] = _parse_doc(template, markdown_files[doc["filename"]])
    try:
        return preview_from_source(original, translations, language=language, title=title)
    except (XhtmlTemplateError, EpubPreserveBuildError) as exc:
        raise EpubMarkedMarkdownError(f"structure-preserving preview blocked: {exc}") from exc


def json_manifest(manifest: Mapping) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
