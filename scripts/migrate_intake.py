"""Conservative intake auditor. Never rewrites original drafts or review evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

CHAPTER = re.compile(r"^chapter-(\d{3})-ru-(?:draft|full-draft)(?:\.part-[\w-]+)?\.(?:md|json)$", re.I)
FULL_MD = re.compile(r"^chapter-(\d{3})-ru-(?:draft|full-draft)\.md$", re.I)
INDEX = re.compile(r"<!--\s*(?:source-block\s+|p:)(\d+)\s*-->")
DIGEST = re.compile(r"(?i)(?:original\s+epub\s+sha-256|sha-256\s+of\s+original)\s*:\s*[^0-9a-f]*([0-9a-f]{64})")
CANONICAL = ("metadata.json", "progress.json", "source-manifest.json",
             "source-revisions.json", "review-ledger.json")


class MigrationError(ValueError):
    pass


def checksum(file: Path) -> str:
    h = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_source(book: Path) -> str | None:
    note = book / "INTAKE.md"
    if not note.is_file():
        return None
    match = DIGEST.search(note.read_text(encoding="utf-8"))
    return match.group(1).lower() if match else None


def spine_entries(epub: Path) -> list[dict]:
    try:
        with zipfile.ZipFile(epub) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            opf_name = next((n.get("full-path") for n in container.iter()
                             if n.tag.split("}")[-1] == "rootfile"), None)
            if not opf_name:
                raise MigrationError("EPUB does not declare an OPF")
            opf = ET.fromstring(archive.read(opf_name))
            manifest = {x.get("id"): x for x in opf.iter()
                        if x.tag.split("}")[-1] == "item"}
            entries = []
            for node in opf.iter():
                if node.tag.split("}")[-1] != "itemref":
                    continue
                item = manifest.get(node.get("idref"))
                if item is None or item.get("media-type") not in ("application/xhtml+xml", "text/html"):
                    continue
                if "nav" in item.get("properties", "").split():
                    continue
                entry = (PurePosixPath(opf_name).parent /
                         unquote(item.get("href", "").split("#")[0])).as_posix()
                document = ET.fromstring(archive.read(entry))
                block_elements = [n for n in document.iter()
                                  if n.tag.split("}")[-1] in {"h1", "h2", "h3", "h4", "p"}]
                all_blocks = len(block_elements)
                nonempty_blocks = sum(bool("".join(n.itertext()).strip()) for n in block_elements)
                first_block_tag = block_elements[0].tag.split("}")[-1] if block_elements else None
                entries.append({"basename": PurePosixPath(entry).name, "blocks": all_blocks,
                                "all_blocks": all_blocks, "nonempty_blocks": nonempty_blocks,
                                "first_block_tag": first_block_tag})
            if not entries:
                raise MigrationError("No readable EPUB spine units")
            return entries
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise MigrationError("Unusable source EPUB: " + str(exc)) from exc


def resolve_block_scheme(indexes: set[int], spine_unit: dict):
    """Identify source-block numbering; do not silently mix empty and nonempty schemes."""
    if not indexes:
        return None
    all_count = spine_unit["all_blocks"]
    nonempty_count = spine_unit["nonempty_blocks"]
    if indexes == set(range(all_count)):
        return "all_xhtml_blocks"
    if indexes == set(range(nonempty_count)):
        return "nonempty_xhtml_blocks"
    # Historical p:000 indexes may start at 'Chapter Notes' because the source
    # XHTML h2 chapter heading was intentionally handled outside block markers.
    # This is an UNREVIEWED candidate, not proof that a translated title exists.
    if (spine_unit.get("first_block_tag") in {"h1", "h2", "h3", "h4"}
            and all_count > 2 and indexes == set(range(all_count - 1))):
        return "all_xhtml_blocks_excluding_heading"
    return None


def merge_indexed(parts: list[Path]):
    result = {}
    sources = set()
    issues = []
    for file in parts:
        try:
            obj = json.loads(file.read_text(encoding="utf-8"))
            mapping = obj.get("draft", obj.get("translation_by_source_index"))
            if not isinstance(mapping, dict):
                continue
            source = obj.get("source_path", obj.get("source_file"))
            if source:
                sources.add(PurePosixPath(str(source)).name)
            for key, value in mapping.items():
                if not str(key).isdigit() or not isinstance(value, str):
                    issues.append(f"{file.name}: malformed index/value")
                elif int(key) in result and result[int(key)] != value:
                    issues.append(f"{file.name}: conflicting block {key}")
                else:
                    result[int(key)] = value
        except (OSError, UnicodeError, ValueError, AttributeError) as exc:
            issues.append(f"{file.name}: {exc}")
    return result, sorted(sources), issues



def merge_markdown(parts: list[Path]):
    """Reassemble source-indexed Markdown fragments without trusting ordering names."""
    merged = {}
    errors = []
    for file in sorted(parts):
        text = file.read_text(encoding="utf-8")
        markers = list(INDEX.finditer(text))
        for i, marker in enumerate(markers):
            idx = int(marker.group(1))
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            value = text[marker.end():end].strip()
            if idx in merged and merged[idx] != value:
                errors.append(f"{file.name}: conflicting Markdown block {idx}")
            else:
                merged[idx] = value
    return merged, errors


def load_source_reference(path: Path, book: Path, expected: str | None):
    """Read metadata-only source index, never authorizing canonical staging."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationError("Unreadable source reference: " + str(exc)) from exc
    if not expected or payload.get("schema_version") != 1 or payload.get("book_slug") != book.name:
        raise MigrationError("Reference schema/book identity mismatch")
    if payload.get("source_sha256") != expected:
        raise MigrationError("Reference original EPUB digest mismatch")
    spine = payload.get("spine")
    if not isinstance(spine, list) or not spine or payload.get("source_units") != len(spine):
        raise MigrationError("Reference spine is invalid")
    seen = set()
    result = []
    for i, obj in enumerate(spine):
        if not isinstance(obj, dict):
            raise MigrationError("Invalid source reference unit " + str(i))
        basename = obj.get("basename")
        count = obj.get("blocks")
        nonempty = obj.get("nonempty_blocks")
        digest = obj.get("sha256")
        if (not isinstance(basename, str) or not basename or "/" in basename or
                basename in seen or not isinstance(count, int) or count < 1 or
                not isinstance(nonempty, int) or not (0 <= nonempty <= count) or
                not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise MigrationError("Invalid source reference unit " + str(i))
        seen.add(basename)
        result.append({"basename": basename, "blocks": count, "all_blocks": count,
                       "nonempty_blocks": nonempty, "sha256": digest})
    return result


def audit(book: Path, source: Path | None = None, offset: int | None = None,
          source_reference: Path | None = None) -> dict:
    intake = book / "incoming"
    if not intake.is_dir():
        raise MigrationError("Missing incoming directory")
    expected = expected_source(book)
    state = {"book": book.name, "expected_source_sha256": expected,
             "source_verified": False, "blockers": [], "missing_canonical_files":
             [p for p in CANONICAL if not (book / p).is_file()],
             "chapters": [], "ready_candidates": 0, "reviewed_promotions": 0}
    spine = None
    state["source_map_loaded"] = False
    if source is not None and source_reference is not None:
        raise MigrationError("Choose either a trusted EPUB or a metadata reference, not both")
    if source is None and source_reference is not None:
        spine = load_source_reference(source_reference, book, expected)
        state["source_map_loaded"] = True
        state["source_units"] = len(spine)
        state["blockers"].append("Reference-only audit: original EPUB not provided for canonical preflight")
    elif source is None:
        state["blockers"].append("Trusted original EPUB is not available")
    elif not source.is_file() or not expected:
        state["blockers"].append("Original source file or trusted digest missing")
    else:
        state["provided_source_sha256"] = checksum(source)
        if state["provided_source_sha256"] != expected:
            state["blockers"].append("Source SHA-256 mismatch")
        else:
            spine = spine_entries(source)
            state["source_verified"] = True
            state["source_units"] = len(spine)
    if state["missing_canonical_files"]:
        state["blockers"].append("Canonical book state not initialized")
    numbered = {}
    for path in sorted(intake.iterdir()):
        match = re.match(r"chapter-(\d{3})-ru-", path.name)
        if match and path.is_file():
            numbered.setdefault(int(match.group(1)), []).append(path)
    for number, paths in sorted(numbered.items()):
        full = [p for p in paths if FULL_MD.fullmatch(p.name)]
        json_parts = [p for p in paths if p.suffix == ".json" and "-ru-" in p.name]
        md_parts = [p for p in paths if p.suffix == ".md" and p not in full]
        indexed, sources, problems = merge_indexed(json_parts)
        md_indexed, md_issues = merge_markdown(md_parts)
        problems.extend(md_issues)
        if indexed and md_indexed:
            for idx in set(indexed) & set(md_indexed):
                if indexed[idx] != md_indexed[idx]:
                    problems.append(f"Cross-format conflict at block {idx}")
            indexed = {**md_indexed, **indexed}
        elif md_indexed:
            indexed = md_indexed
        candidate = str(full[0].relative_to(book)) if len(full) == 1 else None
        method = "markdown" if candidate else None
        if not candidate and indexed and not problems:
            candidate = "indexed_parts"
            method = "indexed_parts"
        if len(full) > 1:
            problems.append("Multiple full draft files")
        unit = None
        blocks = None
        all_blocks = None
        nonempty_blocks = None
        block_scheme = None
        if spine is not None and offset is not None:
            pos = number - 1 + offset
            if pos < 0 or pos >= len(spine):
                problems.append("Chapter outside source spine")
            else:
                unit = pos + 1
                all_blocks = spine[pos]["all_blocks"]
                nonempty_blocks = spine[pos]["nonempty_blocks"]
                if sources and any(s != spine[pos]["basename"] for s in sources):
                    problems.append("Draft source path differs from verified EPUB")
                if method == "indexed_parts":
                    block_scheme = resolve_block_scheme(set(indexed), spine[pos])
                    if block_scheme is None:
                        problems.append(f"Indexed coverage {len(indexed)}/{all_blocks} all blocks "
                                        f"or {nonempty_blocks} nonempty blocks")
                elif method == "markdown":
                    markers = [int(x) for x in INDEX.findall((book / candidate).read_text(encoding="utf-8"))]
                    if markers:
                        block_scheme = resolve_block_scheme(set(markers), spine[pos])
                        if len(markers) != len(set(markers)) or block_scheme is None:
                            problems.append(f"Markdown indexed coverage mismatch: {len(markers)}/"
                                            f"{all_blocks} all or {nonempty_blocks} nonempty")
                blocks = (all_blocks if block_scheme == "all_xhtml_blocks"
                          else nonempty_blocks if block_scheme == "nonempty_xhtml_blocks"
                          else all_blocks - 1 if block_scheme == "all_xhtml_blocks_excluding_heading"
                          else all_blocks)
        if not candidate:
            problems.append("No unambiguous complete candidate")
        status = ("source_unverified" if spine is None else
                  "needs_chapter_offset" if offset is None else
                  "needs_reconciliation" if problems else
                  "reference_candidate_unreviewed" if state["source_map_loaded"] else
                  "candidate_unreviewed")
        if status == "candidate_unreviewed":
            state["ready_candidates"] += 1
        elif status == "reference_candidate_unreviewed":
            state["reference_candidates"] = state.get("reference_candidates", 0) + 1
        state["chapters"].append({"chapter": number, "unit_number": unit, "status": status,
                                  "candidate": candidate, "method": method, "source_blocks": blocks,
                                  "indexed_blocks": len(indexed),
                                  "all_source_blocks": all_blocks,
                                  "nonempty_source_blocks": nonempty_blocks,
                                  "index_scheme": block_scheme,
                                  "missing_heading_requires_review": block_scheme == "all_xhtml_blocks_excluding_heading",
                                  "indexed_min": min(indexed) if indexed else None,
                                  "indexed_max": max(indexed) if indexed else None,
                                  "files": len(paths), "issues": problems})
    auxiliary = book / "incoming" / "afterword-ru-draft.json"
    if auxiliary.is_file():
        try:
            payload = json.loads(auxiliary.read_text(encoding="utf-8"))
            block_map = payload.get("draft", {})
            if not isinstance(block_map, dict) or not all(str(i).isdigit() and isinstance(v, str) for i, v in block_map.items()):
                raise MigrationError("afterword has invalid indexed mapping")
            afterword = {"path": "incoming/afterword-ru-draft.json",
                         "indexed_blocks": len(block_map), "status": "source_unverified",
                         "unit_number": None, "issues": []}
            if spine is not None and offset is not None:
                matches = [i for i, unit in enumerate(spine)
                           if unit["basename"] == PurePosixPath(payload.get("source_path", "")).name]
                if len(matches) != 1 or matches[0] != len(spine) - 1:
                    afterword["issues"].append("Afterword must uniquely match final source spine unit")
                else:
                    pos = matches[0]
                    afterword["unit_number"] = pos + 1
                    afterword["index_scheme"] = resolve_block_scheme(
                        {int(i) for i in block_map}, spine[pos])
                    if afterword["index_scheme"] is None:
                        afterword["issues"].append("Afterword coverage mismatch")
                    afterword["status"] = ("needs_reconciliation" if afterword["issues"] else
                                           "reference_candidate_unreviewed" if state["source_map_loaded"] else
                                           "candidate_unreviewed")
            state["afterword"] = afterword
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            state["afterword"] = {"status": "needs_reconciliation", "issues": [str(exc)]}
    return state


def stage(book: Path, state: dict, offset: int) -> list[str]:
    if not state["source_verified"] or state["missing_canonical_files"] or state["blockers"]:
        raise MigrationError("Refusing to stage before verified source and canonical initialization")
    root = book.parent.parent
    for command in (
        [sys.executable, "scripts/book.py", "validate", book.name],
        [sys.executable, "scripts/corpus.py", "verify", book.name],
    ):
        result = subprocess.run(command, cwd=root, capture_output=True, text=True)
        if result.returncode:
            raise MigrationError("Canonical preflight failed: " + " ".join(command[1:]) +
                                 " " + (result.stdout + result.stderr)[-600:])
    staged = []
    for ch in state["chapters"]:
        if ch["status"] != "candidate_unreviewed":
            continue
        if ch["method"] == "markdown":
            data = (book / ch["candidate"]).read_bytes()
        else:
            paths = sorted((book / "incoming").glob(f"chapter-{ch['chapter']:03d}-ru-*"))
            json_parts = [p for p in paths if p.suffix == ".json"]
            md_parts = [p for p in paths if p.suffix == ".md"]
            parts, _, issues = merge_indexed(json_parts)
            md, md_issues = merge_markdown(md_parts)
            issues.extend(md_issues)
            for idx in set(parts) & set(md):
                if parts[idx] != md[idx]:
                    issues.append(f"Cross-format conflict at block {idx}")
            parts = {**md, **parts}
            if issues or set(parts) != set(range(ch["source_blocks"])):
                raise MigrationError("Indexed draft changed since audit")
            data = ("\n\n".join(f"<!-- source-block {i} -->\n{parts[i]}"
                                 for i in sorted(parts)) + "\n").encode("utf-8")
        dest = book / "migration-staging" / f"{ch['unit_number']:03d}-chapter-{ch['chapter']:03d}-UNREVIEWED.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.read_bytes() != data:
                raise MigrationError("Staging conflict: " + str(dest))
        else:
            with dest.open("xb") as out:
                out.write(data)
        staged.append(str(dest.relative_to(book)))
    afterword = state.get("afterword")
    if afterword and afterword["status"] == "candidate_unreviewed":
        obj = json.loads((book / afterword["path"]).read_text(encoding="utf-8"))
        mapped = {int(k): v for k, v in obj["draft"].items()}
        data = ("\n\n".join(f"<!-- source-block {i} -->\n{mapped[i]}"
                           for i in sorted(mapped)) + "\n").encode("utf-8")
        target = book / "migration-staging" / f"{afterword['unit_number']:03d}-afterword-UNREVIEWED.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise MigrationError("Staging conflict: " + str(target))
        else:
            with target.open("xb") as out:
                out.write(data)
        staged.append(str(target.relative_to(book)))
    return staged


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--source", type=Path)
    sources.add_argument("--source-reference", type=Path,
                         help="Metadata-only source index; never authorizes staging")
    parser.add_argument("--chapter-offset", type=int)
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.slug):
            raise MigrationError("Invalid book slug")
        if args.chapter_offset is not None and args.chapter_offset < 0:
            raise MigrationError("Negative chapter offset")
        book = args.root / "books" / args.slug
        report = audit(book, args.source, args.chapter_offset,
                       source_reference=args.source_reference)
        if args.stage:
            if args.chapter_offset is None:
                raise MigrationError("Staging requires explicit chapter offset")
            report["staged"] = stage(book, report, args.chapter_offset)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (MigrationError, OSError, UnicodeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
