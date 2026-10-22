"""Opt-in source-based EPUB preview packaging, without workflow state writes.

Caller must supply a complete source-bound block translation map. This module
preserves original non-text resources byte-for-byte and checks link targets;
EPUB nav/NCX localization and review/build admission belong to later PRs.
"""

from __future__ import annotations

import io
import zipfile
from typing import Mapping
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .epub_source_map import EpubSourceMapError, _name, _path, inventory_epub
from .xhtml_template import XhtmlTemplateError, inspect_xhtml, render_xhtml
from .epub_preserve_validation import EpubPreservationError, verify_preservation


class EpubPreserveBuildError(ValueError):
    """Cannot safely assemble the requested translated EPUB preview."""


def _xml(raw: bytes, path: str) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EpubPreserveBuildError(f"invalid output XHTML/OPF {path}: {exc}") from exc


def _language(root: ET.Element, language: str) -> None:
    root.attrib["{http://www.w3.org/XML/1998/namespace}lang"] = language
    root.attrib["lang"] = language


def _replace_package(raw: bytes, language: str, title: str | None) -> bytes:
    root = _xml(raw, "package.opf")
    metadata = next((x for x in root if _name(x.tag) == "metadata"), None)
    if metadata is None:
        raise EpubPreserveBuildError("source OPF has no metadata")
    langs = [x for x in metadata if _name(x.tag) == "language"]
    if len(langs) != 1:
        raise EpubPreserveBuildError("source OPF requires exactly one dc:language")
    langs[0].text = language
    if title is not None:
        titles = [x for x in metadata if _name(x.tag) == "title"]
        if len(titles) != 1:
            raise EpubPreserveBuildError("source OPF requires exactly one dc:title")
        titles[0].text = title
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _check_links(files: Mapping[str, bytes], xhtml_paths: set[str]) -> None:
    ids: dict[str, set[str]] = {}
    for path in xhtml_paths:
        root = _xml(files[path], path)
        found: set[str] = set()
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            ident = node.attrib.get("id")
            if ident:
                if ident in found:
                    raise EpubPreserveBuildError(f"duplicate XHTML id {ident!r} in {path}")
                found.add(ident)
        ids[path] = found
    for path in xhtml_paths:
        root = _xml(files[path], path)
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            href = node.attrib.get("href")
            if not href:
                continue
            split = urlsplit(href)
            if split.scheme:
                if split.scheme.lower() not in {"https", "http", "mailto"}:
                    raise EpubPreserveBuildError(f"unsafe external href: {path}")
                continue
            if split.netloc:
                raise EpubPreserveBuildError(f"network-relative href unsupported: {path}")
            target = _path(split.path, base=path) if split.path else path
            if target not in files:
                raise EpubPreserveBuildError(f"broken relative href {href!r} in {path}")
            if split.fragment:
                if target not in ids or split.fragment not in ids[target]:
                    raise EpubPreserveBuildError(f"broken fragment href {href!r} in {path}")


def preview_from_source(
    original: bytes,
    translations: Mapping[str, Mapping[str, str]],
    *,
    language: str,
    title: str | None = None,
) -> bytes:
    """Build a structurally checked preview from original EPUB+complete text map.

    Does not change canonical progress, statuses, acceptance, or review evidence.
    Navigation strings remain original until localized by the later integration.
    """
    if not language or not isinstance(language, str) or not language.isalpha():
        raise EpubPreserveBuildError("language must be a nonempty alphabetic language tag")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise EpubPreserveBuildError("title must be meaningful text")
    try:
        source_map = inventory_epub(original)
    except EpubSourceMapError as exc:
        raise EpubPreserveBuildError(f"original EPUB source blocked: {exc}") from exc
    spine = {x["path"]: x for x in source_map["spine"]}
    if set(translations) != set(spine):
        raise EpubPreserveBuildError("translations must cover exactly every spine XHTML document")
    with zipfile.ZipFile(io.BytesIO(original), "r") as archive:
        infos = archive.infolist()
        if infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
            raise EpubPreserveBuildError("EPUB mimetype must be first and uncompressed")
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        for path in spine:
            try:
                template = inspect_xhtml(files[path])
                transformed = render_xhtml(files[path], template, translations[path])
            except XhtmlTemplateError as exc:
                raise EpubPreserveBuildError(f"cannot translate {path}: {exc}") from exc
            root = _xml(transformed, path)
            _language(root, language)
            files[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        package_path = source_map["opf_path"]
        files[package_path] = _replace_package(files[package_path], language, title)
        xhtml_resources = {resource["path"] for resource in source_map["resources"]
                           if resource["media_type"] == "application/xhtml+xml"}
        _check_links(files, xhtml_resources)
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as output:
            for item in infos:
                if item.is_dir():
                    output.writestr(item, b"")
                else:
                    output.writestr(item, files[item.filename])
        out = result.getvalue()
        with zipfile.ZipFile(io.BytesIO(out)) as check:
            if check.testzip() is not None:
                raise EpubPreserveBuildError("output ZIP CRC failed")
        try:
            verify_preservation(original, out)
        except EpubPreservationError as exc:
            raise EpubPreserveBuildError(f"preservation validation blocked: {exc}") from exc
        return out
