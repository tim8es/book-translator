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
                blocks = sum(n.tag.split("}")[-1] in {"h1", "h2", "h3", "h4", "p"}
                             for n in document.iter())
                entries.append({"basename": PurePosixPath(entry).name, "blocks": blocks})
            if not entries:
                raise MigrationError("No readable EPUB spine units")
            return entries
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise MigrationError("Unusable source EPUB: " + str(exc)) from exc


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


def audit(book: Path, source: Path | None = None, offset: int | None = None) -> dict:
    intake = book / "incoming"
    if not intake.is_dir():
        raise MigrationError("Missing incoming directory")
    expected = expected_source(book)
    state = {"book": book.name, "expected_source_sha256": expected,
             "source_verified": False, "blockers": [], "missing_canonical_files":
             [p for p in CANONICAL if not (book / p).is_file()],
             "chapters": [], "ready_candidates": 0, "reviewed_promotions": 0}
    spine = None
    if source is None:
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
        json_parts = [p for p in paths if CHAPTER.fullmatch(p.name) and p.suffix == ".json"]
        indexed, sources, problems = merge_indexed(json_parts)
        candidate = str(full[0].relative_to(book)) if len(full) == 1 else None
        method = "markdown" if candidate else None
        if not candidate and indexed and not problems:
            candidate = "indexed_json_parts"
            method = "indexed_json"
        if len(full) > 1:
            problems.append("Multiple full draft files")
        unit = None
        blocks = None
        if spine is not None and offset is not None:
            pos = number - 1 + offset
            if pos < 0 or pos >= len(spine):
                problems.append("Chapter outside source spine")
            else:
                unit = pos + 1
                blocks = spine[pos]["blocks"]
                if sources and any(s != spine[pos]["basename"] for s in sources):
                    problems.append("Draft source path differs from verified EPUB")
                if method == "indexed_json" and set(indexed) != set(range(blocks)):
                    problems.append(f"Indexed coverage {len(indexed)}/{blocks}")
                if method == "markdown":
                    markers = [int(x) for x in INDEX.findall((book / candidate).read_text(encoding="utf-8"))]
                    if markers and (len(markers) != len(set(markers)) or set(markers) != set(range(blocks))):
                        problems.append(f"Markdown index coverage mismatch: {len(markers)}/{blocks}")
        if not candidate:
            problems.append("No unambiguous complete candidate")
        status = ("source_unverified" if spine is None else
                  "needs_chapter_offset" if offset is None else
                  "needs_reconciliation" if problems else "candidate_unreviewed")
        if status == "candidate_unreviewed":
            state["ready_candidates"] += 1
        state["chapters"].append({"chapter": number, "unit_number": unit, "status": status,
                                  "candidate": candidate, "method": method, "source_blocks": blocks,
                                  "indexed_blocks": len(indexed), "files": len(paths), "issues": problems})
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
            paths = sorted((book / "incoming").glob(f"chapter-{ch['chapter']:03d}-ru-draft*.json"))
            parts, _, issues = merge_indexed(paths)
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
    return staged


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--chapter-offset", type=int)
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.slug):
            raise MigrationError("Invalid book slug")
        if args.chapter_offset is not None and args.chapter_offset < 0:
            raise MigrationError("Negative chapter offset")
        book = args.root / "books" / args.slug
        report = audit(book, args.source, args.chapter_offset)
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
