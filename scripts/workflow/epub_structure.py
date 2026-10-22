"""Structure-preserving EPUB translation: translate prose, retain original XHTML.

The sole translatable surface is an ordered stream of complete text blocks.
Protected markers preserve links, inline emphasis, images and hard breaks.
Any missing/altered marker or unmatched source text fails closed.
Only Python standard-library modules are required.
"""
from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import zipfile
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

PROFILE = "epub-structural-v1"
STRUCTURE_PATH = "layout/structure.json"
XHTML = "http://www.w3.org/1999/xhtml"
DC = "http://purl.org/dc/elements/1.1/"
OPF = "http://www.idpf.org/2007/opf"
BLOCKS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre",
    "div", "section", "article", "aside", "blockquote", "figcaption",
    "td", "th", "dt", "dd", "caption", "address", "header", "footer",
}
CONTAINERS = BLOCKS | {"body", "ol", "ul", "table", "tbody", "thead", "tfoot", "tr", "dl", "main", "figure", "nav"}
VOID = {"br", "img", "hr", "wbr", "source"}
SKIP = {"script", "style", "svg", "math"}
SEG = re.compile(r"(?m)^⟦BT:SEG:(\d{6})⟧$")
MARK = re.compile(r"⟦BT:([OCV]):(\d{4})⟧")
TOKEN = re.compile(r"⟦BT:[^⟧]*⟧")
ET.register_namespace("", XHTML)
ET.register_namespace("epub", "http://www.idpf.org/2007/ops")


class StructureError(ValueError):
    """Source cannot be losslessly mapped to translatable text blocks."""


def name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def member_path(base: str, href: str) -> str:
    value = unquote(href.split("?", 1)[0].split("#", 1)[0])
    if not value or value.startswith("/") or "\\" in value:
        raise StructureError(f"unsafe EPUB member reference: {href!r}")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), value))
    if path in (".", "..") or path.startswith("../"):
        raise StructureError(f"EPUB reference escapes archive: {href!r}")
    return path


_DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_SVG = re.compile(rb"<(?:[a-zA-Z][\w.-]*:)?svg(?:\s|>)", re.IGNORECASE)
_REMOTE_CSS = re.compile(
    rb"(?:url\(\s*['\"]?\s*|@import\s+['\"]?\s*)(?:https?:|//|data:|javascript:)",
    re.IGNORECASE,
)


def _safe_xml(raw: bytes, path: str = "EPUB XML") -> ET.Element:
    """Reject DTD/entity declarations in every XML package member."""
    if _DTD.search(raw):
        raise StructureError(f"DTD/entity declarations are not supported: {path}")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise StructureError(f"invalid XML in {path}: {exc}") from exc


def _validate_navigation(body: ET.Element, path: str) -> None:
    """Treat only syntactically constrained EPUB navigation as excluded text."""
    nav_count = 0
    navigation_sections: list[ET.Element] = []
    text_tags = {"a", "span", "h1", "h2", "h3", "h4", "h5", "h6",
                 "em", "strong", "i", "b", "small", "abbr", "cite", "sup", "sub"}
    allowed = text_tags | {"nav", "ol", "ul", "li", "br"}

    def navigation(node: ET.Element) -> None:
        tag = name(node.tag)
        if tag not in allowed:
            raise StructureError(f"unmapped prose/element <{tag}> inside navigation: {path}")
        if tag == "nav":
            raise StructureError(f"nested navigation is not supported: {path}")
        if tag == "a" and not node.get("href"):
            raise StructureError(f"navigation link without target: {path}")
        if node.get("hidden") is not None or node.get("aria-hidden") == "true":
            raise StructureError(f"hidden navigation prose is unsupported: {path}")
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
                     node.get("style", ""), re.IGNORECASE):
            raise StructureError(f"hidden navigation CSS is unsupported: {path}")
        if (node.text or "").strip() and tag not in text_tags:
            raise StructureError(f"unmapped text inside navigation <{tag}>: {path}")
        if tag == "li" and not any(name(x.tag) in {"a", "span"} for x in node.iter() if x is not node):
            raise StructureError(f"navigation list item without label: {path}")
        for child in node:
            navigation(child)
            if (child.tail or "").strip() and tag not in text_tags:
                raise StructureError(f"unmapped text between navigation elements: {path}")

    def outside(node: ET.Element) -> None:
        nonlocal nav_count
        if name(node.tag) == "nav":
            nav_count += 1
            navigation_sections.append(node)
            # First scan all body siblings for unmapped prose. Validate the
            # navigation contents afterwards so an outside-text violation
            # cannot be masked by a malformed nav list.
            return
        if (node.text or "").strip():
            raise StructureError(f"navigation has text outside its nav: {path}")
        for child in node:
            outside(child)
            if (child.tail or "").strip():
                raise StructureError(f"navigation has text outside its nav: {path}")
    outside(body)
    if nav_count < 1:
        raise StructureError(f"navigation document has no nav section: {path}")
    for section in navigation_sections:
        if not any(name(x.tag) in {"ol", "ul"} for x in section.iter()):
            raise StructureError(f"navigation without a link list: {path}")
        for child in section:
            navigation(child)
            if (child.tail or "").strip():
                raise StructureError(f"unmapped text after nav children: {path}")
        if (section.text or "").strip():
            raise StructureError(f"unmapped text in nav container: {path}")


def _validate_xhtml_security(raw: bytes, path: str) -> None:
    """Fail closed on executable XHTML and unsafe URI/attribute constructs."""
    root = _safe_xml(raw, path)
    disallowed = {"script", "iframe", "object", "embed", "form", "input",
                  "canvas", "audio", "video", "svg", "math"}
    for element in root.iter():
        tag = name(element.tag)
        if tag in disallowed:
            raise StructureError(f"unsupported active XHTML element <{tag}>: {path}")
        if tag == "meta":
            # In particular, http-equiv=refresh initiates navigation without
            # any user activation. Keep only inert historical content-type.
            equivalence = next((value.strip().lower() for key, value in element.attrib.items()
                                if name(key) == "http-equiv"), None)
            safe_directives = {"content-type", "x-ua-compatible", "content-language",
                               "content-style-type", "default-style"}
            if equivalence is not None and equivalence not in safe_directives:
                raise StructureError(f"unsafe meta HTTP directive {equivalence!r}: {path}")
        for attribute, value in element.attrib.items():
            key = name(attribute)
            if key.startswith("on") or key in {"srcdoc", "base"}:
                raise StructureError(f"unsafe XHTML attribute {key}: {path}")
            if key in {"srcset", "ping", "action", "formaction", "poster"}:
                raise StructureError(f"unsupported active XHTML attribute {key}: {path}")
            if key in {"href", "src"}:
                url = urlsplit(value.strip())
                # User-activated external hyperlinks are permitted; automatically
                # fetched resources (img/link/etc.) must be local to the EPUB.
                user_link = tag in {"a", "area"} and key == "href"
                if user_link and url.scheme.lower() in {"https", "http", "mailto"}:
                    continue
                if url.scheme or url.netloc or value.strip().startswith("//"):
                    raise StructureError(f"remote/unsafe XHTML resource URI in {key}: {path}")
                if url.path:
                    member_path(path, url.path)
            if key == "style" and _REMOTE_CSS.search(value.encode("utf-8")):
                raise StructureError(f"remote inline CSS resource is unsupported: {path}")


def manifest_and_spine(archive: zipfile.ZipFile):
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise StructureError("duplicate EPUB archive members")
    total_uncompressed = 0
    for member in archive.infolist():
        path = member.filename
        pieces = path.split("/")
        if (not path or path.startswith("/") or "\\" in path or "\x00" in path
                or any(part in {"", ".", ".."} for part in pieces[:-1])
                or (not member.is_dir() and pieces[-1] in {"", ".", ".."})
                or (len(path) >= 2 and path[1] == ":")):
            raise StructureError(f"unsafe EPUB archive member path: {path!r}")
        # Do not republish symlink entries or permit oversized decompressed data.
        file_type = (member.external_attr >> 16) & 0o170000
        if file_type == 0o120000:
            raise StructureError(f"EPUB archive symlink is not permitted: {path!r}")
        if member.file_size > 64 * 1024 * 1024:
            raise StructureError(f"oversized EPUB resource: {path!r}")
        total_uncompressed += member.file_size
        if total_uncompressed > 512 * 1024 * 1024:
            raise StructureError("EPUB exceeds safe uncompressed archive size")
    # Older EPUB archives may omit the mimetype member. Preserve their
    # content but emit a standards-compliant first, uncompressed member.
    if "mimetype" in names and archive.read("mimetype") != b"application/epub+zip":
        raise StructureError("invalid EPUB mimetype")
    container = _safe_xml(archive.read("META-INF/container.xml"), "META-INF/container.xml")
    roots = [e.attrib.get("full-path") for e in container.iter() if name(e.tag) == "rootfile"]
    if len(roots) != 1 or not roots[0] or roots[0].startswith("/") or ".." in roots[0].split("/"):
        raise StructureError("ambiguous or unsafe package path")
    opf_path = roots[0]
    package = _safe_xml(archive.read(opf_path), opf_path)
    manifest = {}
    for item in package.iter():
        if name(item.tag) != "item":
            continue
        identifier = item.get("id")
        if not identifier or identifier in manifest or not item.get("href"):
            raise StructureError("invalid or duplicate package item")
        manifest[identifier] = (member_path(opf_path, item.get("href")), item)
    spine = []
    for ref in package.iter():
        if name(ref.tag) != "itemref":
            continue
        entry = manifest.get(ref.get("idref"))
        if entry is None:
            raise StructureError("unknown spine entry")
        path, item = entry
        if item.get("media-type") != "application/xhtml+xml":
            raise StructureError(f"unsupported spine media type: {path}")
        if path in spine or path not in names:
            raise StructureError(f"duplicate or missing spine XHTML: {path}")
        spine.append(path)
    if not spine:
        raise StructureError("empty EPUB spine")
    known = {path for path, _ in manifest.values()}
    for resource_path in names:
        suffix = resource_path.lower()
        if suffix.endswith((".js", ".mjs", ".svg", ".svgz")):
            raise StructureError(f"executable EPUB archive member is unsupported: {resource_path}")
        if resource_path.endswith("/"):
            continue
        # Source MIME can be wrong. Reject embedded SVG markup even if mislabeled.
        if _SVG.search(archive.read(resource_path)[:4096]):
            raise StructureError(f"embedded SVG markup is unsupported: {resource_path}")
        if suffix.endswith((".xhtml", ".html", ".htm")) and resource_path not in known:
            raise StructureError(f"XHTML outside the EPUB manifest: {resource_path}")
    for resource_path, item in manifest.values():
        media_type = item.get("media-type", "").lower()
        if (media_type in {"application/javascript", "text/javascript",
                           "application/ecmascript", "text/ecmascript", "image/svg+xml"}
                or resource_path.lower().endswith((".js", ".mjs", ".svg", ".svgz"))):
            raise StructureError(f"executable EPUB resource is unsupported: {resource_path}")
        if media_type == "text/html":
            raise StructureError(f"HTML resource without XHTML coverage is unsupported: {resource_path}")
        if media_type == "text/css" and _REMOTE_CSS.search(archive.read(resource_path)):
            raise StructureError(f"remote CSS resource is unsupported: {resource_path}")
        if (media_type == "application/xhtml+xml"
                or resource_path.lower().endswith((".xhtml", ".html", ".htm"))):
            _validate_xhtml_security(archive.read(resource_path), resource_path)
    for path in names:
        if path.lower().endswith((".xhtml", ".html", ".htm")) and path not in known:
            raise StructureError(f"XHTML outside the EPUB manifest: {path}")
    for path, item in manifest.values():
        if path not in names:
            raise StructureError(f"missing manifested resource: {path}")
        if item.get("media-type") == "application/xhtml+xml" and path not in spine:
            root = _safe_xml(archive.read(path), path)
            body = next((e for e in root.iter() if name(e.tag) == "body"), None)
            if body is None:
                raise StructureError(f"off-spine XHTML is missing body: {path}")
            if "nav" in item.get("properties", "").split():
                _validate_navigation(body, path)
            elif "".join(body.itertext()).strip():
                raise StructureError(f"XHTML contains untranslated text outside EPUB spine: {path}")
    return opf_path, package, manifest, spine


def _leaves(root: ET.Element) -> list[ET.Element]:
    bodies = [e for e in root.iter() if name(e.tag) == "body"]
    if len(bodies) != 1:
        raise StructureError("XHTML document must contain one body")
    body = bodies[0]
    leaves = []

    def walk(node: ET.Element):
        tag = name(node.tag)
        if tag in SKIP:
            raise StructureError(f"unsupported body element <{tag}>")
        child_blocks = [c for c in node if name(c.tag) in CONTAINERS]
        if tag in BLOCKS and not child_blocks:
            if "".join(node.itertext()).strip() or any(name(c.tag) in VOID for c in node.iter()):
                leaves.append(node)
            return
        if tag not in CONTAINERS:
            raise StructureError(f"unsupported body structure <{tag}> outside text block")
        if node.text and node.text.strip():
            raise StructureError(f"unmapped text before child blocks in <{tag}>")
        for c in node:
            walk(c)
            if c.tail and c.tail.strip():
                raise StructureError(f"unmapped text after child block in <{tag}>")

    walk(body)
    if not leaves:
        raise StructureError("no translatable body blocks")
    return leaves


def _transparent_span(block: ET.Element) -> ET.Element | None:
    """A no-attribute wrapper enclosing exactly one complete prose segment.

    Preserve the wrapper in XHTML without making the translator copy two
    otherwise meaningless tokens for every paragraph.
    """
    if (len(block) == 1 and not (block.text or "")
            and name(block[0].tag) == "span" and not block[0].attrib
            and not len(block[0]) and not (block[0].tail or "")):
        return block[0]
    return None


def _inline_source(block: ET.Element):
    wrapper = _transparent_span(block)
    if wrapper is not None:
        text = wrapper.text or ""
        if TOKEN.search(text):
            raise StructureError("source includes reserved BT marker syntax")
        return text, {}
    lookup: dict[int, ET.Element] = {}
    counter = 0

    def visit(node: ET.Element) -> str:
        nonlocal counter
        parts = [node.text or ""]
        for child in node:
            child_name = name(child.tag)
            if child_name in BLOCKS or child_name in SKIP:
                raise StructureError(f"nested/unsupported inline element <{child_name}>")
            if TOKEN.search(child.text or "") or TOKEN.search(child.tail or ""):
                raise StructureError("source includes reserved BT marker syntax")
            counter += 1
            key = counter
            if key > 9999:
                raise StructureError("too many inline nodes in one block")
            lookup[key] = child
            empty = not (child.text or len(child))
            if child_name in VOID or empty:
                parts.append(f"⟦BT:V:{key:04d}⟧")
            else:
                parts.append(f"⟦BT:O:{key:04d}⟧")
                parts.append(visit(child))
                parts.append(f"⟦BT:C:{key:04d}⟧")
            parts.append(child.tail or "")
        return "".join(parts)

    if TOKEN.search(block.text or ""):
        raise StructureError("source includes reserved BT marker syntax")
    return visit(block), lookup


def segments(root: ET.Element) -> list[str]:
    return [_inline_source(node)[0] for node in _leaves(root)]


def chapter_text(raw: bytes) -> tuple[str, str, int]:
    root = _safe_xml(raw, "spine XHTML")
    chunks = segments(root)
    titles = [("".join(n.itertext()).strip()) for n in _leaves(root) if name(n.tag) in {"h1", "h2"}]
    title = next((t for t in titles if t), "Chapter")
    return title, "\n\n".join(
        f"⟦BT:SEG:{i:06d}⟧\n{chunk}" for i, chunk in enumerate(chunks, 1)
    ) + "\n", len(chunks)


def read_chapters(source: Path):
    raw = source.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        _, _, _, paths = manifest_and_spine(archive)
        units = []
        for path in paths:
            try:
                title, text, count = chapter_text(archive.read(path))
            except (StructureError, ET.ParseError) as exc:
                raise StructureError(f"{path}: {exc}") from exc
            units.append((title, text, path, count))
    layout = {
        "schema_version": 1,
        "profile": PROFILE,
        "source_sha256": sha(raw),
        "documents": [{"path": path, "segment_count": count} for _, _, path, count in units],
    }
    return units, layout


def parse_translation(markdown: str, count: int) -> list[str]:
    matches = list(SEG.finditer(markdown))
    if len(matches) != count:
        raise StructureError(f"expected {count} BT text segments, got {len(matches)}")
    if markdown[:matches[0].start()].strip():
        raise StructureError("unexpected content before first segment")
    result = []
    for index, match in enumerate(matches, 1):
        if int(match.group(1)) != index:
            raise StructureError("missing, duplicated or reordered text segment marker")
        end = matches[index].start() if index < len(matches) else len(markdown)
        content = markdown[match.end():end].strip("\r\n")
        if not content.strip() or SEG.search(content) or "⟦BT:SEG:" in content:
            raise StructureError(f"empty, nested or malformed segment {index}")
        result.append(content)
    return result


def _put_text(parent: ET.Element, text: str):
    if len(parent):
        parent[-1].tail = (parent[-1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def replace_block(node: ET.Element, translated: str):
    wrapper = _transparent_span(node)
    if wrapper is not None:
        if TOKEN.search(translated) or "⟦BT:" in translated:
            raise StructureError("unexpected protected token in unstyled paragraph")
        wrapper.text = translated
        return
    _, originals = _inline_source(node)
    encountered: set[int] = set()
    stack: list[tuple[int, ET.Element]] = [(0, node)]
    for child in list(node):
        node.remove(child)
    node.text = None
    cursor = 0
    for match in MARK.finditer(translated):
        literal = translated[cursor:match.start()]
        if TOKEN.search(literal) or "⟦BT:" in literal:
            raise StructureError("unexpected/malformed protected inline token")
        _put_text(stack[-1][1], literal)
        kind, key = match.group(1), int(match.group(2))
        original = originals.get(key)
        if original is None:
            raise StructureError(f"unknown protected inline token {key}")
        if kind in {"O", "V"}:
            if key in encountered:
                raise StructureError(f"repeated protected inline token {key}")
            if kind == "V" and (original.text or len(original)) and name(original.tag) not in VOID:
                raise StructureError(f"inline token {key} must have an opening and closing marker")
            if kind == "O" and not (original.text or len(original)) and name(original.tag) not in VOID:
                raise StructureError(f"empty inline token {key} must remain a void marker")
            clone = ET.Element(original.tag, original.attrib)
            stack[-1][1].append(clone)
            encountered.add(key)
            if kind == "O":
                stack.append((key, clone))
        elif kind == "C":
            if len(stack) == 1 or stack[-1][0] != key:
                raise StructureError(f"unbalanced inline close {key}")
            stack.pop()
        cursor = match.end()
    rest = translated[cursor:]
    if TOKEN.search(rest) or "⟦BT:" in rest:
        raise StructureError("unexpected/malformed protected inline token")
    _put_text(stack[-1][1], rest)
    if len(stack) != 1 or encountered != set(originals):
        raise StructureError("missing protected inline marker or unclosed markup")


def _check_links(archive: zipfile.ZipFile, replaced: Mapping[str, bytes]):
    names = set(archive.namelist())
    def source(path):
        return replaced.get(path) or archive.read(path)
    seen_ids = {}
    paths = []
    # OPF media-type is authoritative for reading XHTML, not its filename.
    _, _, manifest, _ = manifest_and_spine(archive)
    xhtml_paths = {path for path, item in manifest.values()
                   if item.get("media-type") == "application/xhtml+xml"}
    xhtml_paths |= {p for p in names if p.lower().endswith((".xhtml", ".html", ".htm"))}
    for filename in sorted(xhtml_paths):
        root = _safe_xml(source(filename), filename)
        identifiers = [x.get("id") for x in root.iter() if x.get("id")]
        if len(identifiers) != len(set(identifiers)):
            raise StructureError(f"duplicate internal XHTML anchor id: {filename}")
        seen_ids[filename] = set(identifiers)
        paths.append((filename, root))
    for path, root in paths:
        for element in root.iter():
            tag = name(element.tag)
            for attr in ("href", "src"):
                uri = element.get(attr)
                if uri is None or not uri.strip():
                    continue
                url = urlsplit(uri.strip())
                activated = tag in {"a", "area"} and attr == "href"
                if url.scheme or url.netloc or uri.startswith("//"):
                    if activated and url.scheme.lower() in {"https", "http", "mailto"}:
                        continue
                    raise StructureError(f"remote or unsafe automatic resource: {path}: {uri}")
                target = member_path(path, url.path) if url.path else path
                if target not in names:
                    raise StructureError(f"broken internal resource target {path}: {uri}")
                if url.fragment and target in seen_ids and unquote(url.fragment) not in seen_ids[target]:
                    raise StructureError(f"broken internal anchor {path}: {uri}")


def validate_structural_epub(content: bytes, *, expected_documents: int):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if archive.testzip() is not None:
            raise StructureError("EPUB CRC validation failed")
        _, _, _, paths = manifest_and_spine(archive)
        if len(paths) != expected_documents:
            raise StructureError("EPUB reading order count mismatch")
        _check_links(archive, {})
    return {"unit_count": len(paths), "profile": PROFILE}


def build(source: bytes, *, document_paths: Sequence[str], translations: Sequence[str],
          target_language: str, title: str) -> bytes:
    if len(document_paths) != len(translations):
        raise StructureError("translation count does not match EPUB spine")
    replacements: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        opf_path, package, manifest, paths = manifest_and_spine(archive)
        if list(document_paths) != paths:
            raise StructureError("source EPUB spine changed since extraction")
        headings: dict[str, str] = {}
        for path, markdown in zip(paths, translations):
            root = _safe_xml(archive.read(path), path)
            blocks = _leaves(root)
            translated = parse_translation(markdown, len(blocks))
            for block, content in zip(blocks, translated):
                replace_block(block, content)
            root.set("lang", target_language)
            root.set("{http://www.w3.org/XML/1998/namespace}lang", target_language)
            # Localize navigation labels using the *translated* heading at
            # each target anchor, not the first heading of the containing file.
            for node in root.iter():
                if name(node.tag) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    label = "".join(node.itertext()).strip()
                    if not label:
                        continue
                    headings.setdefault(path, label)
                    if node.get("id"):
                        headings[f"{path}#{node.get('id')}"] = label
                elif node.get("id"):
                    nested = next(
                        (x for x in node.iter() if name(x.tag) in
                         {"h1", "h2", "h3", "h4", "h5", "h6"}), None
                    )
                    if nested is not None:
                        label = "".join(nested.itertext()).strip()
                        if label:
                            headings[f"{path}#{node.get('id')}"] = label
            replacements[path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

        for element in package.iter():
            if element.tag == f"{{{DC}}}language":
                element.text = target_language
            elif element.tag == f"{{{DC}}}title":
                element.text = title
        replacements[opf_path] = ET.tostring(package, encoding="utf-8", xml_declaration=True)

        for path, item in manifest.values():
            if "nav" not in item.get("properties", "").split():
                continue
            # A nav document may itself be a translated spine document.
            # Never re-read the original and silently discard that translation.
            nav = _safe_xml(replacements.get(path, archive.read(path)), path)
            for a in nav.iter():
                if name(a.tag) != "a" or not a.get("href"):
                    continue
                url = urlsplit(a.get("href"))
                if url.scheme or url.netloc:
                    continue
                try:
                    target = member_path(path, url.path) if url.path else path
                except StructureError:
                    continue
                label = headings.get(f"{target}#{unquote(url.fragment)}") if url.fragment else headings.get(target)
                if label and not len(a):
                    a.text = label
            replacements[path] = ET.tostring(nav, encoding="utf-8", xml_declaration=True)

        # EPUB 2 navigation is often stored in NCX rather than EPUB 3 nav.xhtml.
        # Resolve navPoint labels against each translated target heading/anchor.
        for ncx_path, item in manifest.values():
            if item.get("media-type") != "application/x-dtbncx+xml":
                continue
            ncx = _safe_xml(archive.read(ncx_path), ncx_path)
            for point in ncx.iter():
                if name(point.tag) != "navpoint":
                    continue
                content = next((n for n in point if name(n.tag) == "content"), None)
                label_node = next((n for n in point if name(n.tag) == "navlabel"), None)
                label_text = (next((n for n in label_node.iter() if name(n.tag) == "text"), None)
                              if label_node is not None else None)
                if content is None or not content.get("src") or label_text is None:
                    raise StructureError(f"malformed NCX navigation entry: {ncx_path}")
                url = urlsplit(content.get("src"))
                if url.scheme or url.netloc:
                    raise StructureError(f"unsafe NCX navigation target: {ncx_path}")
                target = member_path(ncx_path, url.path) if url.path else ncx_path
                if target not in archive.namelist():
                    raise StructureError(f"missing NCX navigation target: {target}")
                if url.fragment:
                    target_root = _safe_xml(replacements.get(target, archive.read(target)), target)
                    if unquote(url.fragment) not in {n.get("id") for n in target_root.iter()}:
                        raise StructureError(f"missing NCX navigation anchor: {url.fragment}")
                title_at_target = (headings.get(f"{target}#{unquote(url.fragment)}")
                                   if url.fragment else headings.get(target))
                if title_at_target:
                    label_text.text = title_at_target
            replacements[ncx_path] = ET.tostring(ncx, encoding="utf-8", xml_declaration=True)

        _check_links(archive, replacements)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dest:
            dest.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            for info in archive.infolist():
                if info.filename == "mimetype":
                    continue
                dest.writestr(info, replacements.get(info.filename, archive.read(info.filename)))
        result = out.getvalue()
    validate_structural_epub(result, expected_documents=len(paths))
    return result


def layout_bytes(layout: Mapping) -> bytes:
    return (json.dumps(layout, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def audit_structural(source: Path, chapters: Sequence[object]) -> dict:
    """Independent original-XHTML vs protected extraction text inventory."""
    units, layout = read_chapters(source)
    if len(units) != len(chapters):
        raise StructureError("EPUB extraction does not cover every spine XHTML")
    documents = []
    with zipfile.ZipFile(source) as archive:
        for index, ((_, expected, path, count), chapter) in enumerate(zip(units, chapters), 1):
            content = getattr(chapter, "content", None)
            if content != expected:
                raise StructureError(f"extracted chapter {index} differs from original XHTML segments")
            raw = archive.read(path)
            root = _safe_xml(raw, path)
            blocks = _leaves(root)
            extracted = parse_translation(content, len(blocks))
            # Do not reuse _inline_source to establish independent text coverage:
            # XML itertext is compared directly to translated-facing segments
            # after removing only reserved protected tokens.
            for block, segment in zip(blocks, extracted):
                original_text = "".join(block.itertext())
                restored_text = TOKEN.sub("", segment)
                if original_text != restored_text:
                    raise StructureError(f"{path}: source text changed during protected extraction")
            flat = " ".join("".join(block.itertext()) for block in blocks)
            tokens = re.findall(r"\w+|[^\w\s]", flat)
            documents.append({
                "path": path, "state": "verified",
                "xhtml_sha256": sha(raw),
                "reference_sha256": sha("\n".join(tokens).encode("utf-8")),
                "text_tokens": len(tokens), "text_blocks": count,
                "chapter_count": 1,
            })
    raw_source = source.read_bytes()
    return {
        "schema_version": 1, "validator": "epub-xhtml-structure-v2",
        "state": "verified", "source_sha256": sha(raw_source),
        "source_size_bytes": len(raw_source), "chapter_count": len(chapters),
        "documents": documents,
        "extracted": [
            {"number": i, "sha256": sha(unit.content.encode("utf-8"))}
            for i, unit in enumerate(chapters, 1)
        ],
    }
