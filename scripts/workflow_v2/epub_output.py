"""Deterministic Workflow v2 EPUB output identity, package building and validation."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Mapping, Sequence
from html import escape as html_escape
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from .claims import canonical_unit_id


BUILD_CONTRACT = "epub-build-v1"
OUTPUT_MANIFEST_PATH = "output/manifest.json"
MIMETYPE = b"application/epub+zip"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ORDERED_ITEM_RE = re.compile(r"^\d+\.\s+(.+)$")
_MANIFEST_KEYS = {
    "schema_version",
    "build_contract",
    "book_slug",
    "format",
    "preview",
    "artifact_path",
    "artifact_sha256",
    "unit_count",
    "input_fingerprint",
    "repository_commit",
    "state_revisions",
}
_STATE_REVISION_KEYS = {"metadata", "progress", "review_ledger"}
_COVER_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


class EpubOutputError(RuntimeError):
    """EPUB output identity, package, or generated delivery metadata is invalid."""


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EpubOutputError(f"{label} must be a non-empty string")
    return value


def _safe_relative_path(value: Any, label: str) -> str:
    path = _nonempty_string(value, label)
    if "\\" in path:
        raise EpubOutputError(f"{label} must be a safe relative POSIX path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise EpubOutputError(f"{label} must be a safe relative POSIX path")
    return path


def _safe_component(value: Any, label: str) -> str:
    component = _nonempty_string(value, label)
    if component in {".", ".."} or "/" in component or "\\" in component:
        raise EpubOutputError(f"{label} must be a safe path component")
    return component


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_bytes(reader: Callable[[str], bytes], path: str, *, kind: str) -> bytes:
    try:
        content = reader(path)
    except (FileNotFoundError, OSError, KeyError) as exc:
        raise EpubOutputError(f"missing {kind}: {path}") from exc
    if not isinstance(content, bytes):
        raise EpubOutputError(f"{kind} reader must return bytes for {path}")
    if not content.strip():
        raise EpubOutputError(f"{kind} is empty: {path}")
    return content


def _workflow_revision(metadata: Mapping[str, Any]) -> str:
    workflow = metadata.get("workflow")
    if not isinstance(workflow, Mapping):
        raise EpubOutputError("metadata workflow is unavailable")
    return _nonempty_string(workflow.get("resolved_revision"), "metadata workflow resolved_revision")


def _resolution_map(resolutions: Sequence[Any]) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for resolution in resolutions:
        number = getattr(resolution, "chapter_number", None)
        if type(number) is not int or number < 1:
            raise EpubOutputError("review resolution chapter_number must be a positive integer")
        if number in result:
            raise EpubOutputError(f"duplicate review resolution for chapter {number}")
        result[number] = resolution
    return result


def build_input_snapshot(
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
    resolutions: Sequence[Any],
    artifact_reader: Callable[[str], bytes],
    *,
    preview: bool,
    cover_reader: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    """Build deterministic relevant-input identity for one EPUB output."""

    if not isinstance(metadata, Mapping):
        raise EpubOutputError("metadata must be an object")
    if not isinstance(progress, Mapping):
        raise EpubOutputError("progress must be an object")
    if type(preview) is not bool:
        raise EpubOutputError("preview must be a boolean")
    if not callable(artifact_reader):
        raise EpubOutputError("artifact_reader must be callable")

    book_slug = _nonempty_string(progress.get("book_slug"), "progress book_slug")
    title = _nonempty_string(metadata.get("title"), "metadata title")
    target_language = _nonempty_string(metadata.get("target_language"), "metadata target_language")
    author = metadata.get("author")
    if author is not None and not isinstance(author, str):
        raise EpubOutputError("metadata author must be a string or null")
    workflow_revision = _workflow_revision(metadata)

    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise EpubOutputError("progress chapters must be an array")
    review_by_number = _resolution_map(resolutions)
    if preview and review_by_number:
        review_by_number = {}

    units: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, Mapping):
            raise EpubOutputError(f"progress chapter {index + 1} must be an object")
        number = chapter.get("number")
        if type(number) is not int or number < 1:
            raise EpubOutputError(f"progress chapter {index + 1} has invalid number")
        if number in seen_numbers:
            raise EpubOutputError(f"duplicate progress chapter number {number}")
        seen_numbers.add(number)

        translation_path = _safe_relative_path(
            chapter.get("translation_path"),
            f"chapter {number} translation_path",
        )
        translation = _read_bytes(artifact_reader, translation_path, kind="translation artifact")
        status = _nonempty_string(chapter.get("status"), f"chapter {number} status")
        unit = {
            "unit_id": canonical_unit_id(number),
            "number": number,
            "title": _nonempty_string(chapter.get("title"), f"chapter {number} title"),
            "slug": _nonempty_string(chapter.get("slug"), f"chapter {number} slug"),
            "translation_path": translation_path,
            "status": status,
            "translation_sha256": _sha256(translation),
        }

        if not preview:
            resolution = review_by_number.get(number)
            if resolution is None:
                raise EpubOutputError(f"missing review resolution for chapter {number}")
            current = getattr(resolution, "current_record", None)
            if current is not None and not isinstance(current, Mapping):
                raise EpubOutputError(f"current review record for chapter {number} must be an object")
            current = current or {}
            unit["review"] = {
                "state": _nonempty_string(getattr(resolution, "state", None), f"chapter {number} review state"),
                "source_sha256": getattr(resolution, "source_sha256", None),
                "translation_sha256": getattr(resolution, "translation_sha256", None),
                "workflow_revision": current.get("workflow_revision"),
                "review_contract_revision": current.get("review_contract_revision"),
            }
        units.append(unit)

    if not preview and set(review_by_number) != seen_numbers:
        extras = sorted(set(review_by_number) - seen_numbers)
        if extras:
            raise EpubOutputError(f"review resolutions reference unknown chapters: {extras}")

    cover_path = metadata.get("cover_path")
    cover: dict[str, Any] | None = None
    if cover_path is not None:
        cover_path = _safe_relative_path(cover_path, "metadata cover_path")
        reader = cover_reader or artifact_reader
        cover_bytes = _read_bytes(reader, cover_path, kind="cover artifact")
        cover = {"path": cover_path, "sha256": _sha256(cover_bytes)}

    return {
        "build_contract": BUILD_CONTRACT,
        "book_slug": book_slug,
        "format": "epub",
        "preview": preview,
        "metadata": {
            "title": title,
            "author": author,
            "target_language": target_language,
            "cover_path": cover_path,
        },
        "workflow_revision": workflow_revision,
        "units": units,
        "cover": cover,
        "build_config": {"version": 1},
    }


def input_fingerprint(snapshot: Mapping[str, Any]) -> str:
    if not isinstance(snapshot, Mapping):
        raise EpubOutputError("build input snapshot must be an object")
    try:
        content = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EpubOutputError(f"build input snapshot is not canonical JSON data: {exc}") from exc
    return _sha256(content)


def validate_output_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate strict generated output-manifest v1 data and return a detached copy."""

    if not isinstance(manifest, Mapping):
        raise EpubOutputError("output manifest must be an object")
    if set(manifest) != _MANIFEST_KEYS:
        missing = sorted(_MANIFEST_KEYS - set(manifest))
        extra = sorted(set(manifest) - _MANIFEST_KEYS)
        raise EpubOutputError(f"output manifest fields mismatch: missing={missing} extra={extra}")
    if manifest.get("schema_version") != 1 or type(manifest.get("schema_version")) is not int:
        raise EpubOutputError("output manifest schema_version must be 1")
    if manifest.get("build_contract") != BUILD_CONTRACT:
        raise EpubOutputError(f"output manifest build_contract must be {BUILD_CONTRACT}")
    book_slug = _nonempty_string(manifest.get("book_slug"), "output manifest book_slug")
    if "/" in book_slug or "\\" in book_slug or book_slug in {".", ".."}:
        raise EpubOutputError("output manifest book_slug must be one directory name")
    if manifest.get("format") != "epub":
        raise EpubOutputError("output manifest format must be epub")
    if type(manifest.get("preview")) is not bool:
        raise EpubOutputError("output manifest preview must be a boolean")

    artifact_path = _safe_relative_path(manifest.get("artifact_path"), "output manifest artifact_path")
    if not artifact_path.startswith("output/") or not artifact_path.endswith(".epub"):
        raise EpubOutputError("output manifest artifact_path must be output/*.epub")
    artifact_sha = _nonempty_string(manifest.get("artifact_sha256"), "output manifest artifact_sha256")
    if not _SHA256_RE.fullmatch(artifact_sha):
        raise EpubOutputError("output manifest artifact_sha256 must be lowercase SHA-256")
    fingerprint = _nonempty_string(manifest.get("input_fingerprint"), "output manifest input_fingerprint")
    if not _SHA256_RE.fullmatch(fingerprint):
        raise EpubOutputError("output manifest input_fingerprint must be lowercase SHA-256")

    unit_count = manifest.get("unit_count")
    if type(unit_count) is not int or unit_count < 0:
        raise EpubOutputError("output manifest unit_count must be a non-negative integer")
    repository_commit = manifest.get("repository_commit")
    if repository_commit is not None and (not isinstance(repository_commit, str) or not repository_commit.strip()):
        raise EpubOutputError("output manifest repository_commit must be null or a non-empty string")

    revisions = manifest.get("state_revisions")
    if not isinstance(revisions, Mapping) or set(revisions) != _STATE_REVISION_KEYS:
        raise EpubOutputError("output manifest state_revisions must contain metadata, progress and review_ledger")
    for key in sorted(_STATE_REVISION_KEYS):
        _nonempty_string(revisions.get(key), f"output manifest state_revisions.{key}")
    return copy.deepcopy(dict(manifest))


def build_output_manifest(
    *,
    book_slug: str,
    preview: bool,
    artifact_path: str,
    artifact_sha256: str,
    unit_count: int,
    input_fingerprint: str,
    repository_commit: str | None,
    state_revisions: Mapping[str, str],
) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "build_contract": BUILD_CONTRACT,
        "book_slug": book_slug,
        "format": "epub",
        "preview": preview,
        "artifact_path": artifact_path,
        "artifact_sha256": artifact_sha256,
        "unit_count": unit_count,
        "input_fingerprint": input_fingerprint,
        "repository_commit": repository_commit,
        "state_revisions": dict(state_revisions),
    }
    return validate_output_manifest(manifest)


def resolve_output_status(
    manifest: Mapping[str, Any] | None,
    *,
    artifact_bytes: bytes | None,
    current_fingerprint: str,
    expected_unit_count: int,
) -> dict[str, Any]:
    """Classify a generated artifact/manifest pair without mutating durable state."""

    if manifest is None or artifact_bytes is None:
        return {"state": "missing"}
    if not isinstance(artifact_bytes, bytes):
        return {"state": "invalid", "reason": "artifact content must be bytes"}
    try:
        parsed = validate_output_manifest(manifest)
    except EpubOutputError as exc:
        return {"state": "invalid", "reason": str(exc)}
    if type(expected_unit_count) is not int or expected_unit_count < 0:
        return {"state": "invalid", "reason": "expected unit count is invalid"}
    if parsed["unit_count"] != expected_unit_count:
        return {"state": "invalid", "reason": "manifest unit_count does not match expected units"}
    if _sha256(artifact_bytes) != parsed["artifact_sha256"]:
        return {"state": "invalid", "reason": "artifact SHA-256 does not match output manifest"}
    if parsed["input_fingerprint"] != current_fingerprint:
        return {"state": "stale", "artifact_path": parsed["artifact_path"]}
    return {
        "state": "current",
        "artifact_path": parsed["artifact_path"],
        "artifact_sha256": parsed["artifact_sha256"],
        "preview": parsed["preview"],
    }


def _paragraph(lines: list[str]) -> str:
    text = " ".join(part.strip() for part in lines).strip()
    return f"<p>{html_escape(text)}</p>"


def render_markdown_xhtml(title: str, markdown: str, *, language: str) -> bytes:
    """Render a deterministic, escaped Markdown subset into EPUB-safe XHTML."""

    title = _nonempty_string(title, "chapter title")
    language = _nonempty_string(language, "chapter language")
    if not isinstance(markdown, str):
        raise EpubOutputError("chapter markdown must be text")
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise EpubOutputError("chapter markdown must not be empty")
    lines = normalized.split("\n")
    body: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.strip().startswith("```"):
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            if index >= len(lines):
                raise EpubOutputError("unterminated fenced code block")
            index += 1
            body.append(f"<pre><code>{html_escape(chr(10).join(code))}</code></pre>")
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            body.append(f"<h{level}>{html_escape(heading.group(2))}</h{level}>")
            index += 1
            continue
        if line.startswith("- ") or line.startswith("* "):
            items: list[str] = []
            while index < len(lines) and (lines[index].startswith("- ") or lines[index].startswith("* ")):
                items.append(f"<li>{html_escape(lines[index][2:].strip())}</li>")
                index += 1
            body.append("<ul>" + "".join(items) + "</ul>")
            continue
        ordered = _ORDERED_ITEM_RE.match(line)
        if ordered:
            items = []
            while index < len(lines):
                match = _ORDERED_ITEM_RE.match(lines[index])
                if not match:
                    break
                items.append(f"<li>{html_escape(match.group(1).strip())}</li>")
                index += 1
            body.append("<ol>" + "".join(items) + "</ol>")
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            candidate = lines[index]
            if (
                candidate.strip().startswith("```")
                or _HEADING_RE.match(candidate)
                or candidate.startswith("- ")
                or candidate.startswith("* ")
                or _ORDERED_ITEM_RE.match(candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        body.append(_paragraph(paragraph_lines))

    if not body:
        raise EpubOutputError("chapter markdown rendered no body content")
    escaped_title = html_escape(title)
    escaped_language = html_escape(language, quote=True)
    document = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        f'lang="{escaped_language}" xml:lang="{escaped_language}">\n'
        "<head>\n"
        f"<title>{escaped_title}</title>\n"
        '<link rel="stylesheet" type="text/css" href="../styles.css"/>\n'
        "</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )
    return document.encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _write_zip(zf: zipfile.ZipFile, name: str, content: bytes) -> None:
    zf.writestr(_zip_info(name), content)


def _cover_contract(cover: Mapping[str, Any] | None) -> tuple[str, str, bytes] | None:
    if cover is None:
        return None
    if not isinstance(cover, Mapping):
        raise EpubOutputError("cover must be an object or null")
    name = _safe_component(cover.get("name"), "cover name")
    suffix = PurePosixPath(name).suffix.lower()
    expected_media = _COVER_MEDIA_TYPES.get(suffix)
    if expected_media is None:
        raise EpubOutputError(f"unsupported cover extension: {suffix or '<none>'}")
    media_type = _nonempty_string(cover.get("media_type"), "cover media_type")
    if media_type != expected_media:
        raise EpubOutputError(f"cover media_type must be {expected_media} for {suffix}")
    content = cover.get("content")
    if not isinstance(content, bytes) or not content:
        raise EpubOutputError("cover content must be non-empty bytes")
    return name, media_type, content


def build_epub_bytes(
    *,
    book_slug: str,
    title: str,
    author: str | None,
    language: str,
    units: Sequence[Mapping[str, Any]],
    fingerprint: str,
    cover: Mapping[str, Any] | None = None,
) -> bytes:
    """Build deterministic EPUB 3 bytes from ordered translated units."""

    book_slug = _safe_component(book_slug, "book slug")
    title = _nonempty_string(title, "book title")
    language = _nonempty_string(language, "book language")
    if author is not None and not isinstance(author, str):
        raise EpubOutputError("book author must be text or null")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise EpubOutputError("EPUB fingerprint must be lowercase SHA-256")
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)) or not units:
        raise EpubOutputError("EPUB requires at least one ordered unit")

    chapter_files: list[tuple[str, str, bytes]] = []
    seen_numbers: set[int] = set()
    for unit in units:
        if not isinstance(unit, Mapping):
            raise EpubOutputError("EPUB unit must be an object")
        number = unit.get("number")
        if type(number) is not int or number < 1 or number in seen_numbers:
            raise EpubOutputError("EPUB unit numbers must be unique positive integers")
        seen_numbers.add(number)
        unit_title = _nonempty_string(unit.get("title"), f"unit {number} title")
        slug = _safe_component(unit.get("slug"), f"unit {number} slug")
        markdown = unit.get("markdown")
        if not isinstance(markdown, str):
            raise EpubOutputError(f"unit {number} markdown must be text")
        filename = f"{number:03d}-{slug}.xhtml"
        chapter_files.append(
            (filename, unit_title, render_markdown_xhtml(unit_title, markdown, language=language))
        )

    cover_data = _cover_contract(cover)
    escaped_title = html_escape(title)
    escaped_author = html_escape(author or "")
    escaped_language = html_escape(language)
    identifier = html_escape(f"urn:book-translator:{book_slug}:{fingerprint}")

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
        '</container>\n'
    ).encode("utf-8")

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="styles.css" media-type="text/css"/>',
    ]
    spine_items: list[str] = []
    for filename, _, _ in chapter_files:
        number = int(filename.split("-", 1)[0])
        item_id = f"chapter-{number:03d}"
        manifest_items.append(
            f'<item id="{item_id}" href="text/{html_escape(filename, quote=True)}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{item_id}"/>')
    if cover_data is not None:
        cover_name, cover_media, _ = cover_data
        manifest_items.append(
            f'<item id="cover-image" href="images/{html_escape(cover_name, quote=True)}" '
            f'media-type="{html_escape(cover_media, quote=True)}" properties="cover-image"/>'
        )

    creator = f"    <dc:creator>{escaped_author}</dc:creator>\n" if author and author.strip() else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="book-id">{identifier}</dc:identifier>\n'
        f'    <dc:title>{escaped_title}</dc:title>\n'
        f"{creator}"
        f'    <dc:language>{escaped_language}</dc:language>\n'
        '  </metadata>\n'
        '  <manifest>\n    '
        + "\n    ".join(manifest_items)
        + '\n  </manifest>\n  <spine>\n    '
        + "\n    ".join(spine_items)
        + '\n  </spine>\n</package>\n'
    ).encode("utf-8")

    nav_links = [
        f'<li><a href="text/{html_escape(filename, quote=True)}">{html_escape(unit_title)}</a></li>'
        for filename, unit_title, _ in chapter_files
    ]
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{html_escape(language, quote=True)}" xml:lang="{html_escape(language, quote=True)}">\n'
        f'<head><title>{escaped_title}</title></head>\n<body>\n'
        '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n'
        + "\n".join(nav_links)
        + '\n</ol></nav>\n</body>\n</html>\n'
    ).encode("utf-8")
    css = (
        "body { font-family: serif; line-height: 1.5; }\n"
        "h1, h2, h3, h4, h5, h6 { break-after: avoid; }\n"
        "pre { white-space: pre-wrap; }\n"
    ).encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        _write_zip(zf, "mimetype", MIMETYPE)
        _write_zip(zf, "META-INF/container.xml", container)
        _write_zip(zf, "EPUB/package.opf", opf)
        _write_zip(zf, "EPUB/nav.xhtml", nav)
        _write_zip(zf, "EPUB/styles.css", css)
        for filename, _, xhtml in chapter_files:
            _write_zip(zf, f"EPUB/text/{filename}", xhtml)
        if cover_data is not None:
            cover_name, _, cover_bytes = cover_data
            _write_zip(zf, f"EPUB/images/{cover_name}", cover_bytes)
    content = output.getvalue()
    validate_epub_bytes(content, expected_unit_count=len(chapter_files))
    return content


def _xml(content: bytes, label: str) -> ET.Element:
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise EpubOutputError(f"invalid {label} XML: {exc}") from exc


def _zip_member_path(opf_path: str, href: str, *, label: str) -> str:
    href = _safe_relative_path(href, label)
    path = PurePosixPath(opf_path).parent / PurePosixPath(href)
    if any(part == ".." for part in path.parts):
        raise EpubOutputError(f"{label} escapes EPUB package directory")
    return path.as_posix()


def validate_epub_bytes(content: bytes, *, expected_unit_count: int) -> dict[str, Any]:
    """Validate the structural EPUB contract produced by Workflow v2."""

    if not isinstance(content, bytes) or not content:
        raise EpubOutputError("EPUB content must be non-empty bytes")
    if type(expected_unit_count) is not int or expected_unit_count < 1:
        raise EpubOutputError("expected_unit_count must be a positive integer")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise EpubOutputError(f"invalid EPUB ZIP container: {exc}") from exc

    with archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise EpubOutputError("EPUB mimetype must be the first ZIP member")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise EpubOutputError("EPUB mimetype must be stored without compression")
        if archive.read("mimetype") != MIMETYPE:
            raise EpubOutputError("EPUB mimetype content is invalid")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise EpubOutputError("EPUB contains duplicate ZIP member names")
        if "META-INF/container.xml" not in names:
            raise EpubOutputError("EPUB container.xml is missing")

        container = _xml(archive.read("META-INF/container.xml"), "container.xml")
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None:
            raise EpubOutputError("EPUB container.xml has no rootfile")
        opf_path = rootfile.attrib.get("full-path")
        opf_path = _safe_relative_path(opf_path, "EPUB OPF path")
        if opf_path not in names:
            raise EpubOutputError("EPUB OPF referenced by container.xml is missing")

        package = _xml(archive.read(opf_path), "package.opf")
        if not package.tag.endswith("package") or package.attrib.get("version") != "3.0":
            raise EpubOutputError("EPUB OPF must be a version 3.0 package")
        title = package.find(".//{http://purl.org/dc/elements/1.1/}title")
        language = package.find(".//{http://purl.org/dc/elements/1.1/}language")
        if title is None or not (title.text or "").strip():
            raise EpubOutputError("EPUB OPF title is missing")
        if language is None or not (language.text or "").strip():
            raise EpubOutputError("EPUB OPF language is missing")

        manifest_nodes = package.findall(".//{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item")
        items: dict[str, ET.Element] = {}
        for item in manifest_nodes:
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            media = item.attrib.get("media-type")
            if not item_id or not href or not media or item_id in items:
                raise EpubOutputError("EPUB OPF manifest contains an invalid item")
            items[item_id] = item

        nav_items = [item for item in items.values() if "nav" in item.attrib.get("properties", "").split()]
        if len(nav_items) != 1:
            raise EpubOutputError("EPUB OPF must contain exactly one nav item")
        nav_item = nav_items[0]
        nav_path = _zip_member_path(opf_path, nav_item.attrib["href"], label="EPUB nav href")
        if nav_item.attrib.get("media-type") != "application/xhtml+xml" or nav_path not in names:
            raise EpubOutputError("EPUB nav item is missing or has invalid media type")

        css_items = [item for item in items.values() if item.attrib.get("media-type") == "text/css"]
        if not css_items:
            raise EpubOutputError("EPUB OPF CSS item is missing")
        for css_item in css_items:
            if _zip_member_path(opf_path, css_item.attrib["href"], label="EPUB CSS href") not in names:
                raise EpubOutputError("EPUB CSS resource is missing")

        spine_nodes = package.findall(".//{http://www.idpf.org/2007/opf}spine/{http://www.idpf.org/2007/opf}itemref")
        if len(spine_nodes) != expected_unit_count:
            raise EpubOutputError(
                f"EPUB spine unit count {len(spine_nodes)} does not match expected {expected_unit_count}"
            )
        spine_hrefs: list[str] = []
        for itemref in spine_nodes:
            idref = itemref.attrib.get("idref")
            item = items.get(idref or "")
            if item is None or item.attrib.get("media-type") != "application/xhtml+xml":
                raise EpubOutputError("EPUB spine idref does not resolve to XHTML manifest item")
            href = item.attrib["href"]
            member = _zip_member_path(opf_path, href, label="EPUB chapter href")
            if member not in names:
                raise EpubOutputError(f"EPUB spine chapter is missing: {member}")
            chapter = _xml(archive.read(member), member)
            body = chapter.find(".//{*}body")
            if body is None or not "".join(body.itertext()).strip():
                raise EpubOutputError(f"EPUB chapter body is empty: {member}")
            spine_hrefs.append(href)

        nav = _xml(archive.read(nav_path), "nav.xhtml")
        nav_hrefs = [node.attrib.get("href") for node in nav.findall(".//{*}a") if node.attrib.get("href")]
        if nav_hrefs != spine_hrefs:
            raise EpubOutputError("EPUB nav chapter links do not match spine order")

        cover_items = [
            item for item in items.values() if "cover-image" in item.attrib.get("properties", "").split()
        ]
        if len(cover_items) > 1:
            raise EpubOutputError("EPUB OPF contains multiple cover-image items")
        if cover_items:
            cover_item = cover_items[0]
            cover_path = _zip_member_path(opf_path, cover_item.attrib["href"], label="EPUB cover href")
            suffix = PurePosixPath(cover_path).suffix.lower()
            expected_media = _COVER_MEDIA_TYPES.get(suffix)
            if expected_media is None:
                raise EpubOutputError("EPUB cover-image extension is unsupported")
            if cover_item.attrib.get("media-type") != expected_media:
                raise EpubOutputError(
                    f"EPUB cover-image media type must be {expected_media} for {suffix}"
                )
            if cover_path not in names or not archive.read(cover_path):
                raise EpubOutputError("EPUB cover-image resource is missing or empty")

        return {
            "unit_count": len(spine_nodes),
            "opf_path": opf_path,
            "language": (language.text or "").strip(),
            "has_cover": bool(cover_items),
        }
