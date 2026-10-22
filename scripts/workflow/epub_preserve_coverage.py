"""Independent XHTML-versus-extracted-Marked-Markdown source coverage audit.

This is a staging attestation, NOT the canonical source-coverage.json validator.
The reference text is traversed from XML body directly, independently of the
XHTML -> marked Markdown template extractor. No book prose enters the report.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from io import BytesIO
from typing import Mapping
from xml.etree import ElementTree as ET

from .epub_preserve_markdown import (
    EpubMarkedMarkdownError, _parse_doc, make_markdown_bundle,
)
from .xhtml_template import TOKEN_RE, XhtmlTemplateError, inspect_xhtml, render_xhtml


class MarkedCoverageError(ValueError):
    """Original EPUB XHTML and extracted marked Markdown cannot be certified."""


SPACE_TAGS = {"body", "section", "article", "div", "p", "blockquote", "pre", "h1",
              "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "br", "hr", "ul", "ol", "dl"}
HIDDEN_STYLE = re.compile(r"(?i)(?:display\s*:\s*none|visibility\s*:\s*hidden)")
WORD_PUNCTUATION = re.compile(r"\w+|[^\w\s]")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _tokens(text: str) -> list[str]:
    return WORD_PUNCTUATION.findall(unicodedata.normalize("NFC", text))


def _reference_tokens(xhtml: bytes, path: str) -> list[str]:
    # No re-use of xhtml_template._markup or Markdown block extraction.
    try:
        root = ET.fromstring(xhtml)
    except ET.ParseError as exc:
        raise MarkedCoverageError(f"invalid XHTML XML: {path}: {exc}") from exc
    bodies = [n for n in root.iter() if isinstance(n.tag, str) and _tag(n.tag) == "body"]
    if len(bodies) != 1:
        raise MarkedCoverageError(f"expected one XHTML body: {path}")
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if not isinstance(node.tag, str):
            raise MarkedCoverageError(f"unsupported XML comments/processing instructions: {path}")
        tag = _tag(node.tag)
        if "hidden" in node.attrib or node.attrib.get("aria-hidden", "").lower() == "true":
            raise MarkedCoverageError(f"hidden XHTML body content requires explicit handling: {path}")
        if HIDDEN_STYLE.search(node.attrib.get("style", "")):
            raise MarkedCoverageError(f"CSS-hidden body content requires explicit handling: {path}")
        if tag in SPACE_TAGS:
            parts.append(" ")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if tag in SPACE_TAGS:
            parts.append(" ")

    walk(bodies[0])
    result = _tokens("".join(parts))
    if not result:
        raise MarkedCoverageError(f"empty original XHTML body: {path}")
    return result


def attest_marked_source(
    original: bytes, manifest: Mapping, extracted_markdown: Mapping[str, str],
) -> dict:
    """Prove exact ordered prose-token coverage of unmodified staging extraction."""
    try:
        expected, _ = make_markdown_bundle(original)
    except EpubMarkedMarkdownError as exc:
        raise MarkedCoverageError(f"invalid original EPUB for marked coverage: {exc}") from exc
    if manifest != expected:
        raise MarkedCoverageError("staging manifest does not match exact original EPUB")
    required = {d["filename"] for d in expected["documents"]}
    if set(extracted_markdown) != required:
        raise MarkedCoverageError("source extraction has missing or extra Markdown documents")
    rows: list[dict] = []
    with zipfile.ZipFile(BytesIO(original)) as archive:
        for document in expected["documents"]:
            path = document["path"]
            original_xhtml = archive.read(path)
            reference = _reference_tokens(original_xhtml, path)
            try:
                template = inspect_xhtml(original_xhtml)
                parsed = _parse_doc(template, extracted_markdown[document["filename"]])
                # In addition to prose tokens, reject missing or malformed
                # protected inline elements using the exact XHTML structure.
                render_xhtml(original_xhtml, template, parsed)
            except (XhtmlTemplateError, EpubMarkedMarkdownError) as exc:
                raise MarkedCoverageError(f"invalid mapped source XHTML: {path}: {exc}") from exc
            translated_parts: list[str] = []
            for block in template.blocks:
                plain = TOKEN_RE.sub("", parsed[block.key])
                if "[[BT:" in plain:
                    raise MarkedCoverageError(f"malformed inline token in source extraction: {path}")
                translated_parts.append(plain)
            extracted = _tokens(" ".join(translated_parts))
            if reference != extracted:
                first = next((i for i, (a, b) in enumerate(zip(reference, extracted))
                              if a != b), min(len(reference), len(extracted)))
                raise MarkedCoverageError(
                    f"source tokens differ from independently read XHTML: {path}, "
                    f"first mismatch token={first + 1}, expected={len(reference)}, actual={len(extracted)}"
                )
            rows.append({
                "path": path, "xhtml_sha256": _sha(original_xhtml),
                "markdown_filename": document["filename"],
                "markdown_sha256": _sha(extracted_markdown[document["filename"]].encode("utf-8")),
                "reference_tokens_sha256": _sha("\n".join(reference).encode("utf-8")),
                "token_count": len(reference),
                "block_count": len(template.blocks),
            })
    return {
        "schema_version": 1, "validator": "epub-marked-source-audit-v1",
        "state": "verified_staging_only",
        "source_sha256": _sha(original), "source_size_bytes": len(original),
        "document_count": len(rows), "documents": rows,
    }
