"""Fail-closed, standard-library XHTML text/block template mapping.

This is an opt-in substrate, not an EPUB extractor or a replacement for the
current Markdown workflow. The caller must keep the exact source XHTML bytes,
and must attest OPF/spine, other resources, and all sibling documents separately.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping
from xml.etree import ElementTree as ET


class XhtmlTemplateError(ValueError):
    """Cannot preserve source semantics or safely apply the requested text."""


@dataclass(frozen=True)
class TextBlock:
    key: str
    path: tuple[int, ...]
    source_sha256: str
    text: str


@dataclass(frozen=True)
class DocumentTemplate:
    source_sha256: str
    blocks: tuple[TextBlock, ...]


BLOCK_TAGS = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "pre", "blockquote"}
INLINE_TAGS = {"em", "i", "strong", "b", "code", "span", "a", "sup", "sub", "small", "cite", "q", "abbr", "time", "u", "s", "del", "ins"}
ATOM_TAGS = {"br", "wbr", "img"}
DISALLOWED_TAGS = {"script", "iframe", "object", "embed", "audio", "video", "svg", "math", "form", "input", "button", "canvas"}
TOKEN_RE = re.compile(r"\[\[BT:(OPEN|CLOSE|ATOM):(\d{4})\]\]")
DOCTYPE_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _name(tag: object) -> str:
    if not isinstance(tag, str):
        raise XhtmlTemplateError("XHTML comments/processing instructions in text blocks need dedicated handling")
    return tag.rsplit("}", 1)[-1].lower()


def _parse(raw: bytes) -> ET.Element:
    if not raw or DOCTYPE_RE.search(raw):
        raise XhtmlTemplateError("empty XHTML or DTD/entity declarations are not supported")
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        root = ET.fromstring(raw, parser=parser)
    except ET.ParseError as exc:
        raise XhtmlTemplateError(f"invalid XHTML XML: {exc}") from exc
    if _name(root.tag) != "html":
        raise XhtmlTemplateError("expected an XHTML <html> root")
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = _name(node.tag)
        if tag in DISALLOWED_TAGS:
            raise XhtmlTemplateError(f"unsupported active or complex element <{tag}>")
        for attr, value in node.attrib.items():
            key = _name(attr)
            if key.startswith("on") or key in {"srcdoc"}:
                raise XhtmlTemplateError(f"unsafe XHTML attribute {key}")
            if key in {"href", "src", "action", "formaction"}:
                url = value.strip().lower()
                if url.startswith(("javascript:", "data:", "file:", "vbscript:")):
                    raise XhtmlTemplateError(f"unsafe {key} URI")
    return root


def _body(root: ET.Element) -> ET.Element:
    found = [n for n in root.iter() if isinstance(n.tag, str) and _name(n.tag) == "body"]
    if len(found) != 1:
        raise XhtmlTemplateError("XHTML must have exactly one body")
    return found[0]


def _leaf_blocks(root: ET.Element) -> list[tuple[tuple[int, ...], ET.Element]]:
    body = _body(root)
    selected: list[tuple[tuple[int, ...], ET.Element]] = []

    def walk(node: ET.Element, path: tuple[int, ...], in_body: bool) -> None:
        here = in_body or node is body
        if not here:
            for i, child in enumerate(node):
                if isinstance(child.tag, str):
                    walk(child, path + (i,), False)
            return
        if not isinstance(node.tag, str):
            if (node.tail or "").strip():
                raise XhtmlTemplateError("prose after an unsupported comment/PI")
            return
        tag = _name(node.tag)
        descendant_block = any(
            isinstance(child.tag, str) and _name(child.tag) in BLOCK_TAGS
            for child in node.iter() if child is not node
        )
        if tag in BLOCK_TAGS and not descendant_block:
            if any(text.strip() for text in node.itertext()):
                selected.append((path, node))
            return
        if (node.text or "").strip():
            raise XhtmlTemplateError(f"unmapped prose outside text block at <{tag}>")
        for i, child in enumerate(node):
            walk(child, path + (i,), True)
            if (child.tail or "").strip():
                raise XhtmlTemplateError(f"unmapped tail prose outside text block at <{tag}>")

    walk(root, (), False)
    if not selected:
        raise XhtmlTemplateError("XHTML body has no supported text blocks")
    return selected


def _token(kind: str, ident: int) -> str:
    if ident > 9999:
        raise XhtmlTemplateError("too many inline elements in a text block")
    return f"[[BT:{kind}:{ident:04d}]]"


def _markup(node: ET.Element) -> tuple[str, dict[int, ET.Element]]:
    slots: dict[int, ET.Element] = {}
    parts: list[str] = []

    def literal(text: str | None) -> None:
        if text:
            if "[[BT:" in text:
                raise XhtmlTemplateError("source text conflicts with reserved marker namespace")
            parts.append(text)

    def descend(parent: ET.Element) -> None:
        literal(parent.text)
        for child in parent:
            tag = _name(child.tag)
            if tag in INLINE_TAGS:
                ident = len(slots) + 1
                slots[ident] = child
                parts.append(_token("OPEN", ident))
                descend(child)
                parts.append(_token("CLOSE", ident))
            elif tag in ATOM_TAGS:
                if list(child) or (child.text or "").strip():
                    raise XhtmlTemplateError(f"nonempty atomic <{tag}> element")
                if tag == "img" and (child.attrib.get("alt") or "").strip():
                    raise XhtmlTemplateError("translatable image alt text needs separate support")
                ident = len(slots) + 1
                slots[ident] = child
                parts.append(_token("ATOM", ident))
            else:
                raise XhtmlTemplateError(f"unsupported inline element <{tag}>")
            literal(child.tail)

    descend(node)
    return "".join(parts), slots


def inspect_xhtml(raw: bytes) -> DocumentTemplate:
    """Inventory only leaf text blocks; never mutate source or certify whole EPUB."""
    root = _parse(raw)
    blocks: list[TextBlock] = []
    for index, (path, node) in enumerate(_leaf_blocks(root), 1):
        text, _ = _markup(node)
        if not text.strip():
            raise XhtmlTemplateError(f"empty text block {index}")
        blocks.append(TextBlock(f"b{index:06d}", path, _sha(text.encode("utf-8")), text))
    return DocumentTemplate(_sha(raw), tuple(blocks))


def _add_text(node: ET.Element, text: str) -> None:
    if not text:
        return
    if len(node):
        last = node[-1]
        last.tail = (last.tail or "") + text
    else:
        node.text = (node.text or "") + text


def _replace_block(source: ET.Element, translation: str) -> ET.Element:
    if not isinstance(translation, str) or not translation.strip():
        raise XhtmlTemplateError("translation must be nonempty text")
    _, slots = _markup(source)
    out = ET.Element(source.tag, dict(source.attrib))
    stack: list[tuple[int, ET.Element]] = [(0, out)]
    seen: set[tuple[str, int]] = set()
    index = 0
    for match in TOKEN_RE.finditer(translation):
        literal = translation[index:match.start()]
        if "[[BT:" in literal:
            raise XhtmlTemplateError("invalid or altered protected marker")
        _add_text(stack[-1][1], literal)
        kind, ident = match.group(1), int(match.group(2))
        child = slots.get(ident)
        if child is None:
            raise XhtmlTemplateError(f"unknown protected marker {ident}")
        if (kind, ident) in seen:
            raise XhtmlTemplateError(f"duplicate protected marker {ident}")
        seen.add((kind, ident))
        if kind == "OPEN":
            if _name(child.tag) in ATOM_TAGS:
                raise XhtmlTemplateError("OPEN marker used for atomic element")
            fresh = ET.Element(child.tag, dict(child.attrib))
            stack[-1][1].append(fresh)
            stack.append((ident, fresh))
        elif kind == "CLOSE":
            if len(stack) == 1 or stack[-1][0] != ident:
                raise XhtmlTemplateError("unbalanced or reordered CLOSE marker")
            stack.pop()
        else:
            if _name(child.tag) not in ATOM_TAGS:
                raise XhtmlTemplateError("ATOM marker used for non-atomic element")
            fresh = copy.deepcopy(child)
            fresh.tail = None
            stack[-1][1].append(fresh)
        index = match.end()
    rest = translation[index:]
    if "[[BT:" in rest:
        raise XhtmlTemplateError("invalid or altered protected marker")
    _add_text(stack[-1][1], rest)
    if len(stack) != 1:
        raise XhtmlTemplateError("missing CLOSE marker")
    required = {
        (kind, ident)
        for ident, child in slots.items()
        for kind in (("ATOM",) if _name(child.tag) in ATOM_TAGS else ("OPEN", "CLOSE"))
    }
    if seen != required:
        raise XhtmlTemplateError("missing or mismatched protected marker")
    def signature(node: ET.Element) -> Counter:
        return Counter((x.tag, tuple(sorted(x.attrib.items()))) for x in node.iter())
    if signature(source) != signature(out):
        raise XhtmlTemplateError("inline XHTML element or attribute changed")
    return out


def render_xhtml(raw: bytes, template: DocumentTemplate, translated: Mapping[str, str]) -> bytes:
    """Apply complete, source-bound block translations; do not rewrite absent blocks."""
    if _sha(raw) != template.source_sha256:
        raise XhtmlTemplateError("original XHTML changed after template extraction")
    expected = {block.key for block in template.blocks}
    if set(translated) != expected:
        raise XhtmlTemplateError("missing or unexpected translated block IDs")
    root = _parse(raw)
    located = _leaf_blocks(root)
    if len(located) != len(template.blocks):
        raise XhtmlTemplateError("block count changed after template extraction")
    for (path, block_node), block in zip(located, template.blocks):
        source_text, _ = _markup(block_node)
        if path != block.path or _sha(source_text.encode("utf-8")) != block.source_sha256:
            raise XhtmlTemplateError("block identity mismatch")
        replacement = _replace_block(block_node, translated[block.key])
        # Keep the original tail, which belongs to the parent/container.
        replacement.tail = block_node.tail
        if not path:
            raise XhtmlTemplateError("cannot replace XML root")
        parent = root
        for offset in path[:-1]:
            parent = parent[offset]
        parent[path[-1]] = replacement
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    # Re-parse and independently check that text-block coverage still succeeds.
    if len(inspect_xhtml(result).blocks) != len(template.blocks):
        raise XhtmlTemplateError("translated XHTML has a different text-block count")
    return result
