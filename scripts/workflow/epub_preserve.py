"""Fail-closed, source-layout EPUB translation for graphic-rich XHTML books.

The original package, stylesheets, and raster images are kept byte-for-byte.
Prose units contain stable per-paragraph markers; translated units must retain
these markers and their order before a new package can be assembled.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote
from xml.etree import ElementTree as ET

VALIDATOR = "epub-preserved-xhtml-v1"
MODE = "original_xhtml_v1"
_MARKER = re.compile(r"(?m)^<!-- bt-block:(\d{6}) -->\s*$")
_PROSE = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"}
_ALLOWED = {"html", "head", "title", "meta", "link", "style", "body", "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6", "a", "em", "i", "b", "strong", "br", "img", "svg", "image", "ul", "ol", "li", "blockquote", "pre"}

class PreservedEpubError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", unicodedata.normalize("NFC", text))


def _path(opf: str, href: str) -> str:
    raw = unquote(href.split("#", 1)[0])
    if not raw or raw.startswith("/") or "\\" in raw or "?" in raw:
        raise PreservedEpubError(f"Unsafe EPUB resource reference {href!r}")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(opf), raw))
    if result == ".." or result.startswith("../"):
        raise PreservedEpubError(f"EPUB resource escapes container: {href!r}")
    return result


@dataclass
class Document:
    path: str
    kind: str
    blocks: list[str]
    images: list[dict]
    raw_sha: str
    title: str = ""
    formatted: list[str] | None = None


def _open(epub: Path):
    zf = zipfile.ZipFile(epub)
    names = zf.namelist()
    if len(names) != len(set(names)):
        zf.close()
        raise PreservedEpubError("Duplicate EPUB ZIP entries")
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        opf = next(node.get("full-path") for node in container.iter() if _local(node.tag) == "rootfile")
        package = ET.fromstring(zf.read(opf))
        manifest = {}
        for node in package.iter():
            if _local(node.tag) == "item":
                item_id = node.get("id")
                if not item_id or item_id in manifest:
                    raise PreservedEpubError("Duplicate/empty manifest ID")
                p = _path(opf, node.get("href", ""))
                if p not in names:
                    raise PreservedEpubError(f"Missing declared resource: {p}")
                manifest[item_id] = (p, node.get("media-type", ""), node.get("properties", ""))
        spine = [n.get("idref") for n in package.iter() if _local(n.tag) == "itemref"]
        if not spine or any(id_ not in manifest for id_ in spine) or len(set(spine)) != len(spine):
            raise PreservedEpubError("Malformed EPUB spine")
        return zf, opf, package, manifest, spine
    except Exception:
        zf.close()
        raise


def _blocks(root: ET.Element) -> list[tuple[ET.Element, str]]:
    bodies = [node for node in root.iter() if _local(node.tag) == "body"]
    if len(bodies) != 1:
        raise PreservedEpubError("XHTML must have exactly one body")
    body = bodies[0]
    for node in body.iter():
        name = _local(node.tag)
        if name not in _ALLOWED:
            raise PreservedEpubError(f"Unsupported semantic element <{name}>")
        for key, value in node.attrib.items():
            attr = _local(key)
            if attr.startswith("on") or attr in {"hidden", "srcset"}:
                raise PreservedEpubError(f"Unsupported executable/hidden XHTML attribute {attr}")
            if attr == "style" and re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", value, re.I):
                raise PreservedEpubError("Hidden text requires explicit preservation")
        if name == "a" and not node.get("href", "").startswith(("https://", "http://", "mailto:", "#")):
            # Cross-document TOC is handled separately. Other links are rejected.
            raise PreservedEpubError("Unsupported cross-document hyperlink in prose")
    output = []
    for node in body.iter():
        if _local(node.tag) in _PROSE:
            if any(_local(child.tag) in {"img", "image", "svg"} for child in node.iter() if child is not node):
                raise PreservedEpubError("Inline artwork inside a prose block needs explicit mapping")
            if any(_local(child.tag) in _PROSE for child in node.iter() if child is not node):
                raise PreservedEpubError("Nested prose blocks require explicit mapping")
            text = "".join(node.itertext()).strip()
            if text:
                output.append((node, text))
    # No text outside a mapped block may silently disappear; e.g. standalone div prose.
    mapped = {id(node) for node, _ in output}
    def walk(node, inside=False):
        if id(node) in mapped:
            return
        if (node.text or "").strip() and _local(node.tag) not in {"style", "script"}:
            raise PreservedEpubError(f"Unmapped prose in <{_local(node.tag)}>")
        for child in node:
            walk(child)
            if (child.tail or "").strip():
                raise PreservedEpubError(f"Unmapped tail prose in <{_local(node.tag)}>")
    walk(body)
    return output


def _legacy_toc(root: ET.Element, path: str, spine_paths: set[str]) -> bool:
    body = next((n for n in root.iter() if _local(n.tag) == "body"), None)
    if body is None:
        raise PreservedEpubError(f"{path}: no body")
    anchors = [n for n in body.iter() if _local(n.tag) == "a"]
    if len(anchors) < 2:
        return False
    if any(not n.get("href", "") or n.get("href", "").startswith(("http", "mailto", "#")) for n in anchors):
        return False
    if any(_path(path, n.get("href", "")) not in spine_paths for n in anchors):
        raise PreservedEpubError(f"{path}: TOC reference is outside EPUB spine")
    allowed = {"body", "div", "p", "a", "h1", "h2", "span", "em", "i", "b", "strong"}
    if any(_local(n.tag) not in allowed for n in body.iter()):
        return False
    # TOC is a title followed by paragraphs whose only substantive content is links.
    for node in body.iter():
        if _local(node.tag) in {"p", "div"} and "".join(node.itertext()).strip():
            own = (node.text or "").strip()
            if own and _tokens(own):
                return False
    return True


def analyze(source: Path) -> tuple[list[Document], dict[str, str]]:
    """Inventory source independently of Markdown or translation renderers."""
    zf, opf, package, manifest, spine = _open(source)
    try:
        spine_paths = {manifest[id_][0] for id_ in spine}
        media_by_path = {item[0]: item[1] for item in manifest.values()}
        if len(media_by_path) != len(manifest):
            raise PreservedEpubError("Manifest contains aliases to duplicate resources")
        italic_classes: set[str] = set()
        for asset, media, _ in manifest.values():
            if media == "text/css":
                css = zf.read(asset).decode("utf-8-sig")
                for match in re.finditer(r"\.([a-zA-Z0-9_-]+)\s*\{([^{}]+)\}", css):
                    if re.search(r"font-style\s*:\s*italic(?:\s*;|\s*$)", match.group(2), re.I):
                        italic_classes.add(match.group(1))
        documents = []
        titles = {}
        for id_ in spine:
            path, media, _ = manifest[id_]
            if media != "application/xhtml+xml":
                continue
            root = ET.fromstring(zf.read(path))
            if _legacy_toc(root, path, spine_paths):
                for node in root.iter():
                    if _local(node.tag) == "a" and node.get("href"):
                        title = "".join(node.itertext()).strip()
                        if title:
                            titles[_path(path, node.get("href"))] = title
        for id_ in spine:
            path, media, props = manifest[id_]
            if media != "application/xhtml+xml":
                raise PreservedEpubError(f"Unsupported spine media type {media}: {path}")
            raw = zf.read(path)
            root = ET.fromstring(raw)
            body = next((node for node in root.iter() if _local(node.tag) == "body"), None)
            if body is None:
                raise PreservedEpubError(f"{path}: no body")
            if "nav" in props.split() and not _legacy_toc(root, path, spine_paths):
                raise PreservedEpubError(f"{path}: navigation with substantive or complex content requires mapping")
            if _legacy_toc(root, path, spine_paths):
                documents.append(Document(path, "toc", [], [], _hash(raw), "Contents"))
                continue
            imgs = []
            for img in body.iter():
                if _local(img.tag) not in {"img", "image"}:
                    continue
                href = img.get("src") if _local(img.tag) == "img" else next((v for k, v in img.attrib.items() if _local(k) == "href"), None)
                if not href:
                    raise PreservedEpubError(f"Missing image reference: {path}")
                asset = _path(path, href)
                if media_by_path.get(asset) not in {"image/jpeg", "image/png", "image/gif"}:
                    raise PreservedEpubError(f"Undeclared or unsupported raster asset: {asset}")
                data = zf.read(asset)
                if not data:
                    raise PreservedEpubError(f"Empty image: {asset}")
                imgs.append({"path": asset, "sha256": _hash(data), "media_type": media_by_path[asset]})
            blocks = _blocks(root)
            if not blocks and not imgs:
                raise PreservedEpubError(f"No text or artwork in spine XHTML: {path}")
            img_titles = [n.get("alt", "").strip() for n in body.iter() if _local(n.tag) == "img" and n.get("alt", "").strip()]
            doc_title = img_titles[0] if img_titles else titles.get(path, Path(path).stem)
            documents.append(Document(path, "text" if blocks else "artwork", [text for _, text in blocks], imgs, _hash(raw), doc_title,
                                      [_inline_source(node, italic_classes) for node, _ in blocks]))
        # Check all XHTML in manifest including off-spine, fail closed on substantive text.
        for path, media, props in manifest.values():
            if media != "application/xhtml+xml" or path in spine_paths:
                continue
            root = ET.fromstring(zf.read(path))
            if "".join(root.find(".//{*}body").itertext()).strip():
                raise PreservedEpubError(f"Uncovered off-spine XHTML text: {path}")
        if any(name.lower().endswith((".xhtml", ".html")) and name not in media_by_path for name in zf.namelist()):
            raise PreservedEpubError("Undeclared XHTML outside EPUB manifest")
        # Every referenced resource remains in the package; no document may be dropped.
        return documents, {"opf_path": opf, "source_sha256": _hash(source.read_bytes())}
    except (ET.ParseError, KeyError, zipfile.BadZipFile) as exc:
        raise PreservedEpubError(f"Malformed EPUB XHTML/resource: {exc}") from exc
    finally:
        zf.close()


def has_raster_xhtml(source: Path) -> bool:
    with zipfile.ZipFile(source) as zf:
        return any(name.endswith(".xhtml") and re.search(rb"<(?:img|image)\s", zf.read(name), re.I) for name in zf.namelist())


def _escaped_source(text: str) -> str:
    return "".join("\\" + ch if ch in "\\*`[]" else ch for ch in text)


def _inline_source(node: ET.Element, italic_classes: set[str]) -> str:
    """Represent source italic spans as explicit Markdown emphasis."""
    out = _escaped_source(node.text or "")
    for child in node:
        name = _local(child.tag)
        content = _inline_source(child, italic_classes)
        italic = name in {"em", "i"} or bool(set(child.get("class", "").split()) & italic_classes)
        if italic and content.strip():
            content = "*" + content + "*"
        out += content + _escaped_source(child.tail or "")
    return out.strip()


def _plain_markdown(text: str) -> str:
    # Decode escaped literal Markdown characters and omit generated emphasis.
    text = re.sub(r"(?<!\\)\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\\)\*([^*\n]+)\*", r"\1", text)
    return re.sub(r"\\([\\*`\[\]])", r"\1", text)


def _render_inline(node: ET.Element, content: str) -> None:
    """Render the supported inline emphasis without parsing arbitrary HTML."""
    markup = re.compile(r"(?<!\\)(\*\*([^*\n]+)\*\*|\*([^*\n]+)\*)")
    pos = 0
    prev = None
    def append(text: str):
        nonlocal prev
        text = re.sub(r"\\([\\*`\[\]])", r"\1", text)
        if prev is None:
            node.text = (node.text or "") + text
        else:
            prev.tail = (prev.tail or "") + text
    for match in markup.finditer(content):
        append(content[pos:match.start()])
        tag = "strong" if match.group(2) is not None else "em"
        child = ET.SubElement(node, "{http://www.w3.org/1999/xhtml}" + tag)
        child.text = match.group(2) if match.group(2) is not None else match.group(3)
        prev = child
        pos = match.end()
    append(content[pos:])


def extract(source: Path, factory) -> list[Any]:
    docs, _ = analyze(source)
    result = []
    for doc in docs:
        if doc.kind != "text":
            continue
        paragraphs = [f"<!-- bt-block:{i:06d} -->\n{value}"
                      for i, value in enumerate(doc.formatted or doc.blocks, 1)]
        result.append(factory(title=doc.title, content="\n\n".join(paragraphs) + "\n"))
    if not result:
        raise PreservedEpubError("EPUB has no translatable text blocks")
    return result


def parse_blocks(markdown: str, *, expected: int, allow_source_escapes: bool = False) -> list[str]:
    matches = list(_MARKER.finditer(markdown))
    if len(matches) != expected or [int(m.group(1)) for m in matches] != list(range(1, expected + 1)):
        raise PreservedEpubError("Translated XHTML block markers missing, duplicated or reordered")
    if markdown[:matches[0].start()].strip():
        raise PreservedEpubError("Unmapped text before first paragraph marker")
    parts = []
    for i, match in enumerate(matches):
        end = matches[i+1].start() if i + 1 < len(matches) else len(markdown)
        value = markdown[match.end():end].strip()
        if not value:
            raise PreservedEpubError(f"Empty translated block {i+1}")
        if "<!-- bt-" in value:
            raise PreservedEpubError("Unrecognized nested workflow marker")
        if allow_source_escapes:
            value = _plain_markdown(value)
        parts.append(value)
    return parts


def audit(source: Path, chapters: Sequence[Any]) -> dict[str, Any]:
    docs, identity = analyze(source)
    textual = [d for d in docs if d.kind == "text"]
    if len(textual) != len(chapters):
        raise PreservedEpubError("Coverage differs from extracted reading order")
    extracted = []
    for number, (doc, chapter) in enumerate(zip(textual, chapters), 1):
        if chapter.title != doc.title:
            raise PreservedEpubError(f"Chapter title mismatch for {doc.path}")
        parts = parse_blocks(chapter.content, expected=len(doc.blocks), allow_source_escapes=True)
        for idx, (original, actual) in enumerate(zip(doc.blocks, parts), 1):
            if _tokens(original) != _tokens(actual):
                raise PreservedEpubError(f"{doc.path}: source text differs at paragraph {idx}")
        extracted.append({"number": number, "sha256": _hash(chapter.content.encode("utf-8"))})
    return {"schema_version": 1, "validator": VALIDATOR, "state": "verified",
            "source_sha256": identity["source_sha256"], "source_size_bytes": source.stat().st_size,
            "chapter_count": len(chapters), "documents": [
                {"path": d.path, "state": "verified" if d.kind == "text" else ("preserved_artwork" if d.kind == "artwork" else "preserved_toc"),
                 "xhtml_sha256": d.raw_sha, "text_tokens": sum(len(_tokens(b)) for b in d.blocks),
                 "text_blocks": len(d.blocks), "chapter_count": int(d.kind == "text"),
                 "images": d.images} for d in docs], "extracted": extracted}


def build(source: Path, chapters: Sequence[Mapping[str, Any]], *, expected_source_sha256: str, language: str) -> bytes:
    if _hash(source.read_bytes()) != expected_source_sha256:
        raise PreservedEpubError("Stored EPUB original SHA-256 mismatch")
    docs, identity = analyze(source)
    textual = [d for d in docs if d.kind == "text"]
    if len(textual) != len(chapters):
        raise PreservedEpubError("Translation count differs from source XHTML documents")
    originals = zipfile.ZipFile(source)
    replacements: dict[str, bytes] = {}
    try:
        for doc, chapter in zip(textual, chapters):
            blocks = parse_blocks(chapter["markdown"], expected=len(doc.blocks))
            root = ET.fromstring(originals.read(doc.path))
            root.set("{http://www.w3.org/XML/1998/namespace}lang", language)
            root.set("lang", language)
            nodes = _blocks(root)
            if len(nodes) != len(blocks):
                raise PreservedEpubError(f"Source XHTML block count changed: {doc.path}")
            for (node, original), translated in zip(nodes, blocks):
                # Preserve block tag/class/id and image siblings. Inline content cannot
                # be automatically aligned; reject links unless label survives.
                source_children = list(node)
                drop_cap = (source_children[0] if source_children and _local(source_children[0].tag) == "span"
                            and len("".join(source_children[0].itertext())) == 1
                            and not list(source_children[0]) else None)
                links = [n for n in node.iter() if _local(n.tag) == "a"]
                if len(links) > 1:
                    raise PreservedEpubError(f"Multiple inline links require manual alignment: {doc.path}")
                link = links[0] if links else None
                if link is not None:
                    label = "".join(link.itertext())
                    if translated.count(label) != 1:
                        raise PreservedEpubError(f"Hyperlink label was lost in translation: {doc.path}")
                for child in list(node):
                    node.remove(child)
                node.text = None
                if link is not None:
                    at = translated.index(label)
                    _render_inline(node, translated[:at])
                    new_link = ET.SubElement(node, link.tag, link.attrib)
                    new_link.text = label
                    new_link.tail = _plain_markdown(translated[at + len(label):])
                elif drop_cap is not None and translated and translated[0].isalpha():
                    # Keep the original drop-cap styling, replacing the English
                    # initial with the first letter of the translated paragraph.
                    node.text = None
                    first = ET.SubElement(node, drop_cap.tag, drop_cap.attrib)
                    first.text = translated[0]
                    # Append emphasis after the preserved drop cap.
                    wrapper = ET.Element("{http://www.w3.org/1999/xhtml}span")
                    _render_inline(wrapper, translated[1:])
                    first.tail = wrapper.text
                    node.extend(list(wrapper))
                else:
                    _render_inline(node, translated)
            replacements[doc.path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        opf = identity["opf_path"]
        package = ET.fromstring(originals.read(opf))
        for node in package.iter():
            if _local(node.tag) == "language":
                node.text = language
        replacements[opf] = ET.tostring(package, encoding="utf-8", xml_declaration=True)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as dest:
            for info in originals.infolist():
                dest.writestr(info, replacements.get(info.filename, originals.read(info.filename)))
        result = output.getvalue()
        validate(result, source=source, expected_docs=len(docs))
        return result
    finally:
        originals.close()


def validate(content: bytes, *, source: Path, expected_docs: int) -> dict[str, Any]:
    docs, identity = analyze(source)
    if len(docs) != expected_docs:
        raise PreservedEpubError("EPUB source document count changed")
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(io.BytesIO(content)) as new:
        if old.namelist() != new.namelist():
            raise PreservedEpubError("Output EPUB ZIP member list changed")
        infos = new.infolist()
        if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
            raise PreservedEpubError("EPUB mimetype must be the first uncompressed entry")
        if new.read("mimetype") != b"application/epub+zip":
            raise PreservedEpubError("Output EPUB mimetype mismatch")
        for path in old.namelist():
            if path.lower().endswith((".xhtml", ".html", ".opf")):
                ET.fromstring(new.read(path))
            elif old.read(path) != new.read(path):
                raise PreservedEpubError(f"Asset or stylesheet changed: {path}")
        for d in docs:
            if d.kind != "text" and new.read(d.path) != old.read(d.path):
                raise PreservedEpubError(f"Artwork/TOC XHTML changed: {d.path}")
    return {"unit_count": sum(d.kind == "text" for d in docs), "source_documents": len(docs), "preserved_assets": True}
