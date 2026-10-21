"""Independent fail-closed EPUB XHTML text coverage audit.

A strict ElementTree inventory is compared with the HTMLParser chapter output.
Only the supported textual subset may be certified; unsupported semantics block.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

COVERAGE_PATH = "source-coverage.json"
VALIDATOR = "epub-xhtml-text-v1"
TEXT_MEDIA = {"application/xhtml+xml", "text/html"}
BLOCKS = {"body", "section", "article", "div", "p", "li", "blockquote",
          "pre", "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer"}
UNSUPPORTED = {"a", "img", "table", "figure", "figcaption", "aside", "sup",
               "sub", "math", "ruby", "audio", "video", "svg", "script",
               "style", "nav", "ol", "hr", "iframe", "object", "canvas",
               "form", "input", "button"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_BODY = BLOCKS | {"br", "ul", "span", "em", "i", "strong", "b", "code"}


class SourceCoverageError(ValueError):
    """Cannot prove complete coverage of the EPUB's supported text."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _name(tag: str) -> str:
    if not isinstance(tag, str):
        raise SourceCoverageError("XML comments or processing instructions in body are unsupported")
    return tag.rsplit("}", 1)[-1].lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", unicodedata.normalize("NFC", text))


def _reference(raw: bytes, path: str) -> tuple[list[str], int]:
    """Inventory prose directly from XHTML XML, not the chapter extraction code."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceCoverageError(f"{path}: XHTML cannot be parsed as XML: {exc}") from exc
    bodies = [node for node in root.iter() if _name(node.tag) == "body"]
    if len(bodies) != 1:
        raise SourceCoverageError(f"{path}: XHTML must have exactly one body")
    parts: list[str] = []
    count = 0

    def visit(node: ET.Element) -> None:
        nonlocal count
        tag = _name(node.tag)
        if tag in UNSUPPORTED or tag not in ALLOWED_BODY:
            raise SourceCoverageError(f"{path}: unsupported semantic element <{tag}>")
        for key, value in node.attrib.items():
            attr = _name(key)
            folded = str(value).lower()
            if attr in {"hidden", "href", "src"}:
                raise SourceCoverageError(f"{path}: unsupported attribute {attr}")
            if attr in {"type", "role", "class"} and any(
                term in folded for term in ("footnote", "endnote", "noteref", "pagebreak")
            ):
                raise SourceCoverageError(f"{path}: unsupported annotation {attr}={value!r}")
            if attr in {"style", "aria-hidden"} and any(
                term in folded for term in ("display:none", "display: none", "visibility:hidden",
                                             "visibility: hidden", "true")
            ):
                raise SourceCoverageError(f"{path}: hidden content requires explicit handling")
        if tag in BLOCKS or tag == "br":
            parts.append(" ")
        if tag in {"p", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}:
            count += 1
        if node.text:
            parts.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                parts.append(child.tail)
        if tag in BLOCKS:
            parts.append(" ")

    visit(bodies[0])
    reference = "".join(parts)
    if "*" in reference or chr(96) in reference:
        raise SourceCoverageError(
            f"{path}: literal Markdown delimiters make faithful comparison ambiguous"
        )
    return _tokens(reference), count


def _from_markdown(chapters: Sequence[Any]) -> list[str]:
    parts: list[str] = []
    for chapter in chapters:
        text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+", "", chapter.content)
        text = re.sub(r"(?m)^[ \t]*-[ \t]+", "", text)
        parts.append(text.replace("*", "").replace(chr(96), ""))
    return _tokens(" ".join(parts))


def _archive_path(opf_path: str, href: str) -> str:
    decoded = unquote(href.split("#", 1)[0].split("?", 1)[0])
    if not decoded or decoded.startswith("/") or "\\" in decoded:
        raise SourceCoverageError(f"Unsafe EPUB href {href!r}")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), decoded))
    if result in {".", ".."} or result.startswith("../"):
        raise SourceCoverageError(f"EPUB href escapes archive: {href!r}")
    return result


def audit_epub(
    source: Path,
    chapters: Sequence[Any],
    *,
    extract_document: Callable[[str, str], Sequence[Any]],
    decode_text: Callable[[bytes, str], str],
) -> dict[str, Any]:
    """Certify all linear spine text and reject substantive off-spine XHTML."""
    if not source.is_file() or not zipfile.is_zipfile(source):
        raise SourceCoverageError("Original EPUB is not a valid ZIP archive")
    source_bytes = source.read_bytes()
    with zipfile.ZipFile(source) as archive:
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            opf_path = next(
                (n.attrib.get("full-path") for n in container.iter()
                 if _name(n.tag) == "rootfile"), None
            )
            if not opf_path or opf_path.startswith("/") or ".." in Path(opf_path).parts:
                raise SourceCoverageError("EPUB container has no safe package path")
            opf = ET.fromstring(archive.read(opf_path))
        except (KeyError, ET.ParseError) as exc:
            raise SourceCoverageError(f"Invalid EPUB package: {exc}") from exc

        manifest: dict[str, dict[str, str]] = {}
        for node in opf.iter():
            if _name(node.tag) != "item":
                continue
            item_id, href = node.attrib.get("id"), node.attrib.get("href")
            if not item_id or not href or item_id in manifest:
                raise SourceCoverageError("Missing or duplicate EPUB manifest item")
            manifest[item_id] = {
                "path": _archive_path(opf_path, href),
                "media": node.attrib.get("media-type", ""),
                "properties": node.attrib.get("properties", ""),
            }
        spine = [node.attrib["idref"] for node in opf.iter()
                 if _name(node.tag) == "itemref" and node.attrib.get("idref")]
        if not spine or len(spine) != len(set(spine)):
            raise SourceCoverageError("Empty or duplicated EPUB spine")

        covered: set[str] = set()
        reproduced: list[Any] = []
        documents: list[dict[str, Any]] = []
        for item_id in spine:
            item = manifest.get(item_id)
            if item is None:
                raise SourceCoverageError(f"Unknown EPUB spine idref {item_id}")
            path = item["path"]
            if path in covered:
                raise SourceCoverageError(f"Duplicate spine document {path}")
            covered.add(path)
            if "nav" in item["properties"].split():
                documents.append({"path": path, "state": "excluded_navigation"})
                continue
            if item["media"] not in TEXT_MEDIA:
                raise SourceCoverageError(f"Unsupported EPUB spine media {item['media']!r}: {path}")
            try:
                raw = archive.read(path)
            except KeyError as exc:
                raise SourceCoverageError(f"Missing spine document {path}") from exc
            reference, blocks = _reference(raw, path)
            text = decode_text(raw, path)
            fallback = Path(path).stem.replace("_", " ").replace("-", " ").strip() or item_id
            generated = list(extract_document(text, fallback))
            extracted = _from_markdown(generated)
            if reference != extracted:
                first = next((i for i, pair in enumerate(zip(reference, extracted))
                              if pair[0] != pair[1]), min(len(reference), len(extracted)))
                raise SourceCoverageError(
                    f"{path}: missing, changed or reordered text at token {first + 1} "
                    f"(original={len(reference)}, extracted={len(extracted)})"
                )
            if not reference:
                raise SourceCoverageError(f"{path}: no readable text in spine body")
            reproduced.extend(generated)
            documents.append({
                "path": path, "state": "verified", "xhtml_sha256": _sha(raw),
                "reference_sha256": _sha("\n".join(reference).encode("utf-8")),
                "text_tokens": len(reference), "text_blocks": blocks,
                "chapter_count": len(generated),
            })

        for item in manifest.values():
            if item["media"] not in TEXT_MEDIA or item["path"] in covered:
                continue
            if "nav" in item["properties"].split():
                documents.append({"path": item["path"], "state": "excluded_navigation"})
                continue
            try:
                reference, _ = _reference(archive.read(item["path"]), item["path"])
            except KeyError as exc:
                raise SourceCoverageError(f"Missing off-spine XHTML {item['path']}") from exc
            if reference:
                raise SourceCoverageError(
                    f"{item['path']}: {len(reference)} text tokens outside EPUB spine"
                )
            documents.append({"path": item["path"], "state": "excluded_empty"})

        declared = {item["path"] for item in manifest.values()}
        for member in archive.namelist():
            if member.lower().endswith((".xhtml", ".html", ".htm")) and member not in declared:
                raise SourceCoverageError(
                    f"{member}: XHTML document exists outside the EPUB manifest"
                )

    if len(reproduced) != len(chapters) or any(
        left.title != right.title or left.content != right.content
        for left, right in zip(reproduced, chapters)
    ):
        raise SourceCoverageError("Audited chapter list differs from extracted chapter order/content")
    return {
        "schema_version": 1, "validator": VALIDATOR, "state": "verified",
        "source_sha256": _sha(source_bytes), "source_size_bytes": len(source_bytes),
        "chapter_count": len(chapters), "documents": documents,
        "extracted": [
            {"number": n, "sha256": _sha(chapter.content.encode("utf-8"))}
            for n, chapter in enumerate(chapters, 1)
        ],
    }


def coverage_integrity_errors(
    book_dir: Path, metadata: Mapping[str, Any], progress: Mapping[str, Any]
) -> list[str]:
    """Verify durable coverage report without needing a retained private original."""
    if metadata.get("source_format") != "epub":
        return []
    path = book_dir / COVERAGE_PATH
    if not path.is_file():
        return ["Missing EPUB source-coverage.json; certify the exact original EPUB before translation"]
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"Invalid source-coverage.json: {exc}"]
    if not isinstance(report, dict):
        return ["Invalid source-coverage.json object"]
    errors: list[str] = []
    if (report.get("schema_version"), report.get("validator"), report.get("state")) != (
        1, VALIDATOR, "verified"
    ):
        errors.append("Invalid EPUB source-coverage validator/version/state")
    source = metadata.get("source")
    if not isinstance(source, Mapping):
        errors.append("Missing source identity for EPUB coverage")
    elif report.get("source_sha256") != source.get("sha256") or report.get("source_size_bytes") != source.get("size_bytes"):
        errors.append("EPUB source-coverage identity disagrees with metadata")
    chapters, recorded = progress.get("chapters"), report.get("extracted")
    if not isinstance(chapters, list) or not isinstance(recorded, list):
        return errors + ["Malformed source-coverage chapter records"]
    if report.get("chapter_count") != len(chapters) or len(recorded) != len(chapters):
        return errors + ["EPUB source-coverage chapter count mismatch"]
    docs = report.get("documents")
    if not isinstance(docs, list) or not any(
        isinstance(d, dict) and d.get("state") == "verified" for d in docs
    ):
        errors.append("EPUB source-coverage lacks verified spine documents")
    for chapter, record in zip(chapters, recorded):
        if not isinstance(chapter, Mapping) or not isinstance(record, Mapping):
            errors.append("Invalid EPUB source-coverage chapter entry")
            continue
        n, rel = chapter.get("number"), chapter.get("source_path")
        sha = record.get("sha256")
        if record.get("number") != n or not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            errors.append(f"Chapter {n}: invalid source-coverage fingerprint")
            continue
        if not isinstance(rel, str) or not Path(rel).parts or Path(rel).parts[0] != "extracted" or Path(rel).is_absolute() or ".." in Path(rel).parts:
            errors.append(f"Chapter {n}: invalid extracted artifact path")
            continue
        try:
            actual = _sha((book_dir / rel).read_bytes())
        except OSError:
            errors.append(f"Chapter {n}: extracted source is missing for coverage")
            continue
        if actual != sha:
            errors.append(f"Chapter {n}: source-coverage fingerprint mismatch")
    return errors
