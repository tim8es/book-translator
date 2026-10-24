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
UNSUPPORTED = {"img", "table", "figure", "figcaption", "aside", "sup",
               "sub", "math", "ruby", "audio", "video", "svg", "script",
               "style", "nav", "ol", "hr", "iframe", "object", "canvas",
               "form", "input", "button"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# The same deliberately narrow, unambiguous subset is used by the extractor
# and EPUB renderer. Relative EPUB targets require source-to-output remapping.
LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(((?:https?://|mailto:)[^\s()\[\]]+)\)")
SAFE_HREF_RE = re.compile(r"(?:https?://|mailto:)[^\s()\[\]]+\Z")
ALLOWED_BODY = BLOCKS | {"a", "br", "ul", "span", "em", "i", "strong", "b", "code"}


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


def _reference(raw: bytes, path: str, *, allow_toc: bool = False, allow_svg: bool = False) -> tuple[list[str], int]:
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
        if (tag == "nav" and allow_toc) or (tag == "svg" and allow_svg):
            # Subtrees have already passed independent structural/target checks.
            # Their content is not prose: the output builder regenerates the TOC
            # and the source cover asset is carried separately.
            parts.append(" ")
            return
        if tag in UNSUPPORTED or tag not in ALLOWED_BODY:
            raise SourceCoverageError(f"{path}: unsupported semantic element <{tag}>")
        if tag == "a":
            href = node.attrib.get("href", "")
            if href and not SAFE_HREF_RE.fullmatch(href):
                raise SourceCoverageError(f"{path}: unsupported anchor destination {href!r}")
        for key, value in node.attrib.items():
            attr = _name(key)
            folded = str(value).lower()
            if attr in {"hidden", "src"} or (attr == "href" and tag != "a"):
                raise SourceCoverageError(f"{path}: unsupported attribute {attr}")
            if attr in {"type", "role", "class"} and any(
                term in folded for term in ("footnote", "endnote", "noteref", "pagebreak")
            ):
                raise SourceCoverageError(f"{path}: unsupported annotation {attr}={value!r}")
            if attr == "style" and any(
                term in folded for term in ("display:none", "display: none",
                                             "visibility:hidden", "visibility: hidden")
            ):
                raise SourceCoverageError(f"{path}: hidden content requires explicit handling")
            if attr == "aria-hidden" and folded not in {"true", "false"}:
                raise SourceCoverageError(f"{path}: unsupported aria-hidden value")
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
    return _tokens(reference), count


def _prose_from_markdown(text: str) -> str:
    """Strip generated markup while retaining escaped original punctuation."""
    protected = {chr(92) + char: chr(0xE000 + index)
                 for index, char in enumerate(("*", chr(96), "[", "]", chr(92)))}
    for escaped, sentinel in protected.items():
        text = text.replace(escaped, sentinel)
    text = text.replace("*", "").replace(chr(96), "")
    for escaped, sentinel in protected.items():
        text = text.replace(sentinel, escaped[-1])
    return text


def _from_markdown(chapters: Sequence[Any]) -> list[str]:
    parts: list[str] = []
    for chapter in chapters:
        text = re.sub(r"(?m)^[ \\t]*#{1,6}[ \\t]+", "", chapter.content)
        text = re.sub(r"(?m)^[ \\t]*-[ \\t]+", "", text)
        text = LINK_RE.sub(lambda match: match.group(1), text)
        parts.append(_prose_from_markdown(text))
    return _tokens(" ".join(parts))


def _navigation_is_exclusive(raw: bytes, path: str) -> None:
    """Navigation XHTML is excludable only if body has no non-navigation prose."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceCoverageError(f"{path}: invalid navigation XHTML: {exc}") from exc
    bodies = [node for node in root.iter() if _name(node.tag) == "body"]
    if len(bodies) != 1:
        raise SourceCoverageError(f"{path}: navigation XHTML must have one body")
    outer: list[str] = []
    navigation_count = 0

    def visit(node: ET.Element) -> None:
        nonlocal navigation_count
        if _name(node.tag) == "nav":
            navigation_count += 1
            return
        if node.text:
            outer.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                outer.append(child.tail)

    visit(bodies[0])
    if not navigation_count or _tokens(" ".join(outer)):
        raise SourceCoverageError(
            f"{path}: navigation document contains text outside its nav elements "
            "or has no nav; cannot exclude it from coverage"
        )


def _archive_path(opf_path: str, href: str) -> str:
    decoded = unquote(href.split("#", 1)[0].split("?", 1)[0])
    if not decoded or decoded.startswith("/") or "\\" in decoded:
        raise SourceCoverageError(f"Unsafe EPUB href {href!r}")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), decoded))
    if result in {".", ".."} or result.startswith("../"):
        raise SourceCoverageError(f"EPUB href escapes archive: {href!r}")
    return result


def _verify_toc(root: ET.Element, path: str, archive: zipfile.ZipFile,
                spine_paths: set[str]) -> set[str]:
    """An in-spine TOC is regenerable only with resolvable, unambiguous entries."""
    navs = [node for node in root.iter() if _name(node.tag) == "nav"]
    if not navs:
        return set()
    if len(navs) != 1:
        raise SourceCoverageError(f"{path}: multiple navigation regions unsupported")
    nav = navs[0]
    if not any(_name(key) == "type" and value == "toc"
               for key, value in nav.attrib.items()):
        raise SourceCoverageError(f"{path}: only an explicit epub:type=toc nav is supported")
    allowed = {"nav", "ol", "li", "a", "span", "em", "i", "strong", "b"}
    for node in nav.iter():
        if _name(node.tag) not in allowed:
            raise SourceCoverageError(f"{path}: unsupported TOC element <{_name(node.tag)}>")
        if _name(node.tag) not in {"a", "span", "em", "i", "strong", "b"} and (
            (node.text or "").strip() or (node.tail or "").strip()
        ):
            raise SourceCoverageError(f"{path}: prose inside TOC outside link label")
    entries = [node for node in nav.iter() if _name(node.tag) == "li"]
    if not entries:
        raise SourceCoverageError(f"{path}: empty TOC")
    targets: set[str] = set()
    for li in entries:
        anchors = [node for node in li.iter() if _name(node.tag) == "a"]
        if len(anchors) != 1 or not "".join(anchors[0].itertext()).strip():
            raise SourceCoverageError(f"{path}: ambiguous TOC entry")
        href = anchors[0].attrib.get("href", "")
        if not href or "?" in href or "#" not in href:
            raise SourceCoverageError(f"{path}: TOC link requires a relative document fragment")
        target, fragment = href.split("#", 1)
        if not target or not fragment:
            raise SourceCoverageError(f"{path}: empty TOC target or fragment")
        target_path = _archive_path(path, target)
        if target_path not in spine_paths or target_path in targets:
            raise SourceCoverageError(f"{path}: missing or repeated TOC spine target {target_path}")
        try:
            document = ET.fromstring(archive.read(target_path))
        except (KeyError, ET.ParseError) as exc:
            raise SourceCoverageError(f"{path}: broken TOC target {target_path}") from exc
        if not any(node.attrib.get("id") == unquote(fragment) for node in document.iter()):
            raise SourceCoverageError(f"{path}: broken TOC fragment {fragment}")
        targets.add(target_path)
    return targets


def _verify_svg_cover(root: ET.Element, path: str, archive: zipfile.ZipFile,
                      media_by_path: Mapping[str, str]) -> dict[str, str] | None:
    """Accept only a transparent, image-only SVG cover wrapper; seal raster bytes."""
    svgs = [node for node in root.iter() if _name(node.tag) == "svg"]
    if not svgs:
        return None
    if len(svgs) != 1:
        raise SourceCoverageError(f"{path}: multiple SVG regions unsupported")
    svg = svgs[0]
    children = list(svg)
    if len(children) != 1 or _name(children[0].tag) != "image" or (
        "".join(svg.itertext()).strip()
    ):
        raise SourceCoverageError(f"{path}: SVG cover must contain exactly one image and no prose")
    image = children[0]
    if list(image):
        raise SourceCoverageError(f"{path}: nested image markup unsupported")
    if any(_name(key).startswith("on") for node in (svg, image) for key in node.attrib):
        raise SourceCoverageError(f"{path}: executable SVG attributes unsupported")
    hrefs = [v for k, v in image.attrib.items() if _name(k) == "href"]
    if len(hrefs) != 1:
        raise SourceCoverageError(f"{path}: SVG image must have one href")
    image_path = _archive_path(path, hrefs[0])
    media_type = media_by_path.get(image_path)
    if media_type not in {"image/jpeg", "image/png", "image/gif"}:
        raise SourceCoverageError(f"{path}: cover image not declared as supported raster artwork")
    try:
        data = archive.read(image_path)
    except KeyError as exc:
        raise SourceCoverageError(f"{path}: missing embedded cover {image_path}") from exc
    if not data:
        raise SourceCoverageError(f"{path}: empty embedded cover")
    return {"path": image_path, "media_type": media_type, "sha256": _sha(data)}


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
        members = archive.namelist()
        if len(members) != len(set(members)):
            raise SourceCoverageError("EPUB ZIP has duplicate archive paths")
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

        spine_paths = {manifest[i]["path"] for i in spine if i in manifest}
        media_by_path = {item["path"]: item["media"] for item in manifest.values()}
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
                try:
                    _navigation_is_exclusive(archive.read(path), path)
                except KeyError as exc:
                    raise SourceCoverageError(f"Missing navigation XHTML {path}") from exc
                documents.append({"path": path, "state": "excluded_navigation"})
                continue
            if item["media"] not in TEXT_MEDIA:
                raise SourceCoverageError(f"Unsupported EPUB spine media {item['media']!r}: {path}")
            try:
                raw = archive.read(path)
            except KeyError as exc:
                raise SourceCoverageError(f"Missing spine document {path}") from exc
            try:
                xml_root = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise SourceCoverageError(f"{path}: invalid XHTML: {exc}") from exc
            toc_targets = _verify_toc(xml_root, path, archive, spine_paths)
            cover_image = _verify_svg_cover(xml_root, path, archive, media_by_path)
            reference, blocks = _reference(
                raw, path, allow_toc=bool(toc_targets), allow_svg=cover_image is not None
            )
            text = decode_text(raw, path)
            fallback = Path(path).stem.replace("_", " ").replace("-", " ").strip() or item_id
            generated = list(extract_document(text, fallback))
            extracted = _from_markdown(generated)
            toc_ids = {
                id(node) for nav in xml_root.iter() if _name(nav.tag) == "nav"
                for node in nav.iter()
            }
            original_links = [
                (node.attrib["href"], _tokens("".join(node.itertext())))
                for node in xml_root.iter()
                if id(node) not in toc_ids and _name(node.tag) == "a"
                and node.attrib.get("href")
            ]
            markdown_links = [
                (match.group(2), _tokens(_prose_from_markdown(match.group(1))))
                for chapter in generated
                for match in LINK_RE.finditer(chapter.content)
            ]
            if original_links != markdown_links:
                raise SourceCoverageError(f"{path}: link text or destination lost or changed")
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
                **({"cover_image": cover_image} if cover_image is not None else {}),
                **({"toc_targets": len(toc_targets)} if toc_targets else {}),
            })

        for item in manifest.values():
            if item["media"] not in TEXT_MEDIA or item["path"] in covered:
                continue
            if "nav" in item["properties"].split():
                try:
                    _navigation_is_exclusive(archive.read(item["path"]), item["path"])
                except KeyError as exc:
                    raise SourceCoverageError(f"Missing off-spine navigation XHTML {item['path']}") from exc
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
    book_dir: Path,
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
    *,
    verified_extracted_sha256: Mapping[str, str] | None = None,
) -> list[str]:
    """Verify EPUB coverage, optionally reusing same-preflight verified corpus hashes."""
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
    if not isinstance(docs, list) or not docs:
        errors.append("EPUB source-coverage has no documents")
    else:
        paths: set[str] = set()
        covered_count = 0
        for doc in docs:
            if not isinstance(doc, Mapping):
                errors.append("Invalid EPUB source-coverage document entry")
                continue
            path = doc.get("path")
            if not isinstance(path, str) or not path or path in paths:
                errors.append("Duplicate or invalid EPUB coverage document path")
                continue
            paths.add(path)
            state = doc.get("state")
            if state == "verified":
                covered_count += 1
                if (not isinstance(doc.get("xhtml_sha256"), str)
                        or not SHA_RE.fullmatch(doc["xhtml_sha256"])
                        or not isinstance(doc.get("reference_sha256"), str)
                        or not SHA_RE.fullmatch(doc["reference_sha256"])
                        or type(doc.get("text_tokens")) is not int or doc["text_tokens"] < 1
                        or type(doc.get("text_blocks")) is not int or doc["text_blocks"] < 0
                        or type(doc.get("chapter_count")) is not int or doc["chapter_count"] < 1):
                    errors.append(f"Invalid coverage inventory for {path}")
            elif state not in {"excluded_navigation", "excluded_empty"}:
                errors.append(f"Unsupported EPUB coverage state for {path}")
        if covered_count == 0:
            errors.append("EPUB source-coverage lacks verified spine documents")
        if sum(
            d.get("chapter_count", 0) for d in docs
            if isinstance(d, Mapping) and d.get("state") == "verified"
            and type(d.get("chapter_count")) is int
        ) != len(chapters):
            errors.append("EPUB coverage document chapter totals disagree with progress")
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
        if verified_extracted_sha256 is None:
            try:
                actual = _sha((book_dir / rel).read_bytes())
            except OSError:
                errors.append(f"Chapter {n}: extracted source is missing for coverage")
                continue
        else:
            # Only reuse hashes after verifying the full sealed corpus in this call.
            actual = verified_extracted_sha256.get(rel)
            if actual is None:
                errors.append(f"Chapter {n}: no verified corpus fingerprint for coverage")
                continue
        if actual != sha:
            errors.append(f"Chapter {n}: source-coverage fingerprint mismatch")
    return errors
