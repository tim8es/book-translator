"""Independent source-vs-preview EPUB preservation attestation.

An EPUB preview is only structurally accepted when unchanged resources remain
byte-identical and every non-translated XHTML subtree is equivalent. This is
not a Translator acceptance, independent literary review, or official EPUBCheck.
"""
from __future__ import annotations

import copy
import io
import zipfile
from collections.abc import Mapping
from xml.etree import ElementTree as ET

from .epub_source_map import EpubSourceMapError, inventory_epub
from .xhtml_template import XhtmlTemplateError, _leaf_blocks


class EpubPreservationError(ValueError):
    """Preview changed something other than authorized source text/metadata."""


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""


def _parse(raw: bytes, path: str) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EpubPreservationError(f"invalid XML: {path}: {exc}") from exc


def _package_normalized(raw: bytes, path: str) -> bytes:
    root = _parse(raw, path)
    metadata = next((x for x in root if _tag(x) == "metadata"), None)
    if metadata is None:
        raise EpubPreservationError("OPF metadata is missing")
    for child in metadata:
        if _tag(child) in {"title", "language"}:
            child.text = "__BT_LOCALIZED_METADATA__"
    return ET.tostring(root, encoding="utf-8")


def _outside_text_normalized(raw: bytes, path: str) -> bytes:
    root = _parse(raw, path)
    root.attrib.pop("lang", None)
    root.attrib.pop("{http://www.w3.org/XML/1998/namespace}lang", None)
    try:
        selected = _leaf_blocks(root)
    except XhtmlTemplateError as exc:
        raise EpubPreservationError(f"cannot inventory output text blocks: {path}: {exc}") from exc
    # Erase only the text-bearing leaf subtrees; the enclosing document, head,
    # CSS references, atom/scene separators, structure and attributes stay.
    for idx, (xpath, element) in enumerate(selected, 1):
        if not xpath:
            raise EpubPreservationError("XHTML root cannot be a replaceable block")
        parent = root
        for offset in xpath[:-1]:
            parent = parent[offset]
        # Preserve the text block's own tag/attributes (ids, classes, roles) so
        # mutating an anchor target or formatting class cannot be hidden by masking.
        placeholder = ET.Element(element.tag, dict(element.attrib))
        placeholder.attrib["{urn:book-translator:internal}block-index"] = str(idx)
        # Inline tags may move for translation grammar, but their set and all
        # attributes must remain identical (links, notes, emphases, etc.).
        import hashlib
        import json
        signature = sorted(
            (child.tag, sorted(child.attrib.items()))
            for child in element.iter() if child is not element
        )
        serialized = json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
        placeholder.attrib["{urn:book-translator:internal}inline-hash"] = hashlib.sha256(serialized).hexdigest()
        placeholder.tail = element.tail
        parent[xpath[-1]] = placeholder
    return ET.tostring(root, encoding="utf-8")


def verify_preservation(original: bytes, output: bytes) -> dict[str, object]:
    """Return text-free structural evidence or fail closed on changes outside prose."""
    try:
        report = inventory_epub(original)
    except EpubSourceMapError as exc:
        raise EpubPreservationError(f"cannot attest source EPUB: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(original)) as first, zipfile.ZipFile(io.BytesIO(output)) as second:
            source_names = first.namelist()
            output_names = second.namelist()
            if source_names != output_names or len(output_names) != len(set(output_names)):
                raise EpubPreservationError("EPUB archive resources were added, removed or reordered")
            if (second.infolist()[0].filename != "mimetype"
                    or second.infolist()[0].compress_type != zipfile.ZIP_STORED):
                raise EpubPreservationError("EPUB mimetype ordering/compression changed")
            if second.testzip() is not None:
                raise EpubPreservationError("EPUB output ZIP CRC failed")
            story_paths = {item["path"] for item in report["spine"]}
            opf_path = report["opf_path"]
            changed: list[str] = []
            for path in source_names:
                if path.endswith("/"):
                    continue
                before, after = first.read(path), second.read(path)
                if path == opf_path:
                    if _package_normalized(before, path) != _package_normalized(after, path):
                        raise EpubPreservationError("OPF changed beyond localizable title and language")
                elif path in story_paths:
                    if _outside_text_normalized(before, path) != _outside_text_normalized(after, path):
                        raise EpubPreservationError(f"non-prose XHTML structure changed: {path}")
                elif before != after:
                    raise EpubPreservationError(f"untranslated resource bytes changed: {path}")
                if before != after:
                    changed.append(path)
            if len(changed) > len(story_paths) + 1:
                raise EpubPreservationError("unexpected changed EPUB member count")
            return {
                "validator": "epub-source-preservation-v1",
                "source_sha256": report["source_sha256"],
                "spine_document_count": len(story_paths),
                "resource_count": len(report["resources"]),
                "changed_paths": sorted(changed),
                "unchanged_nontext_resources": True,
            }
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise EpubPreservationError(f"invalid/incomplete output EPUB: {exc}") from exc
