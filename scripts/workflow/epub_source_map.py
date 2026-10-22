"""Read-only EPUB source/package inventory for structure-preserving translation.

The report intentionally contains no book prose. It is *not* a source-coverage
attestation or a durable workflow-state transition; integration is a separate PR.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .xhtml_template import XhtmlTemplateError, inspect_xhtml


class EpubSourceMapError(ValueError):
    """An EPUB package cannot be inventoried without risking a missing resource."""


TEXT_MEDIA = {"application/xhtml+xml"}
OTHER_TEXT_MEDIA = {"text/html"}
ACTIVE_MEDIA = {"application/javascript", "text/javascript", "image/svg+xml"}
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml(raw: bytes, path: str) -> ET.Element:
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.IGNORECASE):
        raise EpubSourceMapError(f"DTD/entity declarations are forbidden: {path}")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EpubSourceMapError(f"invalid XML at {path}: {exc}") from exc


def _path(path: str, *, base: str = "") -> str:
    if not isinstance(path, str) or not path:
        raise EpubSourceMapError("missing package resource path")
    url = urlsplit(path)
    if url.scheme or url.netloc:
        raise EpubSourceMapError(f"external EPUB package resource is unsupported: {path}")
    decoded = unquote(url.path)
    if not decoded or decoded.startswith("/") or "\\" in decoded or "\x00" in decoded:
        raise EpubSourceMapError(f"unsafe EPUB resource path: {path}")
    if any(segment in {".", ".."} for segment in decoded.split("/")):
        raise EpubSourceMapError(f"non-canonical resource path: {path}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base), decoded))
    if resolved.startswith("../") or resolved in {".", ".."}:
        raise EpubSourceMapError(f"resource escapes archive root: {path}")
    return resolved


def _pure_navigation(raw: bytes, path: str) -> None:
    root = _xml(raw, path)
    bodies = [x for x in root.iter() if isinstance(x.tag, str) and _name(x.tag) == "body"]
    if len(bodies) != 1:
        raise EpubSourceMapError(f"navigation must have exactly one body: {path}")
    nav_count = 0
    outside: list[str] = []

    def walk(node: ET.Element) -> None:
        nonlocal nav_count
        if _name(node.tag) == "nav":
            nav_count += 1
            return
        if node.text:
            outside.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                outside.append(child.tail)

    walk(bodies[0])
    if not nav_count or "".join(outside).strip():
        raise EpubSourceMapError(f"navigation has non-navigation prose: {path}")


def inventory_epub(raw: bytes) -> dict[str, Any]:
    """Inventory a full EPUB ZIP and its ordered text units without writing state."""
    if not isinstance(raw, bytes) or not zipfile.is_zipfile(io.BytesIO(raw)):
        raise EpubSourceMapError("source is not an EPUB ZIP")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), "r")
        with archive:
            infos = archive.infolist()
            members = [i.filename for i in infos]
            if len(members) != len(set(members)):
                raise EpubSourceMapError("duplicate ZIP member names")
            if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES or any(i.file_size > MAX_ENTRY_BYTES for i in infos):
                raise EpubSourceMapError("EPUB exceeds safe uncompressed resource limits")
            for member in members:
                if member.endswith("/"):
                    _path(member[:-1])
                else:
                    _path(member)
            if "mimetype" not in members or archive.read("mimetype") != b"application/epub+zip":
                raise EpubSourceMapError("missing or invalid EPUB mimetype")
            if "META-INF/container.xml" not in members:
                raise EpubSourceMapError("missing EPUB container.xml")
            container = _xml(archive.read("META-INF/container.xml"), "META-INF/container.xml")
            roots = [n.attrib.get("full-path") for n in container.iter() if _name(n.tag) == "rootfile"]
            if len(roots) != 1 or not roots[0]:
                raise EpubSourceMapError("EPUB container must select exactly one OPF")
            opf_path = _path(roots[0])
            if opf_path not in members:
                raise EpubSourceMapError("OPF file is missing")
            package = _xml(archive.read(opf_path), opf_path)
            if _name(package.tag) != "package":
                raise EpubSourceMapError("OPF package root is invalid")
            items: dict[str, dict[str, str]] = {}
            paths: set[str] = set()
            for node in package.findall("./{*}manifest/{*}item"):
                ident = node.attrib.get("id")
                href = node.attrib.get("href")
                media = node.attrib.get("media-type")
                if not ident or not href or not media or ident in items:
                    raise EpubSourceMapError("invalid or duplicated manifest item")
                path = _path(href, base=opf_path)
                if path not in members or path in paths:
                    raise EpubSourceMapError(f"missing/duplicated manifest resource: {path}")
                if media in ACTIVE_MEDIA:
                    raise EpubSourceMapError(f"active EPUB resource not yet supported: {path}")
                items[ident] = {"path": path, "media": media, "properties": node.attrib.get("properties", "")}
                paths.add(path)
            if not items:
                raise EpubSourceMapError("empty EPUB manifest")
            refs = [n.attrib.get("idref") for n in package.findall("./{*}spine/{*}itemref")]
            if not refs or None in refs or len(refs) != len(set(refs)):
                raise EpubSourceMapError("missing or duplicate OPF spine references")
            occupied: set[str] = set()
            spine: list[dict[str, Any]] = []
            for index, ident in enumerate(refs, 1):
                item = items.get(ident)
                if item is None:
                    raise EpubSourceMapError(f"unknown spine idref {ident}")
                path = item["path"]
                if item["media"] not in TEXT_MEDIA:
                    raise EpubSourceMapError(f"spine contains unsupported media: {path}")
                if "nav" in item["properties"].split():
                    raise EpubSourceMapError("navigation cannot be a narrative spine unit")
                document = archive.read(path)
                try:
                    template = inspect_xhtml(document)
                except XhtmlTemplateError as exc:
                    raise EpubSourceMapError(f"{path}: {exc}") from exc
                occupied.add(path)
                spine.append({
                    "order": index, "idref": ident, "path": path,
                    "xhtml_sha256": template.source_sha256,
                    "blocks": [{"key": b.key, "path": list(b.path), "source_sha256": b.source_sha256}
                               for b in template.blocks],
                })
            for item in items.values():
                path = item["path"]
                if item["media"] in TEXT_MEDIA and path not in occupied:
                    if "nav" in item["properties"].split():
                        _pure_navigation(archive.read(path), path)
                    else:
                        raise EpubSourceMapError(f"uncovered off-spine XHTML: {path}")
                if item["media"] in OTHER_TEXT_MEDIA:
                    raise EpubSourceMapError(f"HTML not yet supported as EPUB text: {path}")
            declared = set(paths) | {"mimetype", "META-INF/container.xml", opf_path}
            undeclared = [name for name in members if not name.endswith("/") and name not in declared and not name.startswith("META-INF/")]
            if undeclared:
                raise EpubSourceMapError(f"undeclared EPUB archive member: {undeclared[0]}")
            resources = [{"path": obj["path"], "media_type": obj["media"],
                          "sha256": _sha(archive.read(obj["path"]))}
                         for obj in sorted(items.values(), key=lambda o: o["path"])]
            if archive.testzip() is not None:
                raise EpubSourceMapError("EPUB ZIP CRC validation failed")
            return {
                "schema_version": 1, "validator": "epub-xhtml-source-map-v1",
                "source_sha256": _sha(raw), "source_bytes": len(raw),
                "opf_path": opf_path, "spine": spine, "resources": resources,
            }
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise EpubSourceMapError(f"invalid or incomplete EPUB package: {exc}") from exc
