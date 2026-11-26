#!/usr/bin/env python3
"""Export matching reviewed snapshots for blind comparison; never mutates book state."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from blind_quality_comparison import validate_manifest

PUBLIC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PUBLIC / "scripts"))
from pinned_runtime import prepare

SHA40 = re.compile(r"[0-9a-f]{40}")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,199}")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   text=True, stderr=subprocess.DEVNULL, timeout=120).strip()


def clean_head(root):
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("benchmark requires a clean committed state checkout")
    head = git(root, "rev-parse", "HEAD")
    if not SHA40.fullmatch(head):
        raise ValueError("invalid state commit")
    return head


def inside(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("invalid relative artifact path")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("artifact outside book")
    return path


def read_json(root, relative):
    return json.loads(inside(root, relative).read_text("utf-8"))


def checked_snapshot(root, slug, preflight):
    """The supplied preflight must verify the pinned CLI and source corpus."""
    root = root.resolve(strict=True)
    if not SLUG.fullmatch(slug):
        raise ValueError("invalid book slug")
    book = (root / "books" / slug).resolve(strict=True)
    if book != root / "books" / slug or not book.is_dir():
        raise ValueError("unsafe book directory")
    commit = clean_head(root)
    metadata = read_json(book, "metadata.json")
    revision = metadata["workflow"]["resolved_revision"]
    if not SHA40.fullmatch(revision):
        raise ValueError("invalid literary revision")
    status = preflight(root, slug, revision)
    if (status.get("valid") is not True or status.get("errors") != []
            or status.get("claims") != [] or status.get("needs_attention")
            or (status.get("finalization") or {}).get("active") is not False
            or status.get("workflow_revision") != revision
            or (status.get("corpus") or {}).get("state") != "verified"):
        raise ValueError("canonical status is not ready for benchmark")
    chapters = read_json(book, "progress.json")["chapters"]
    units = status["units"]
    if (not chapters or len(chapters) != len(units)
            or len(chapters) > 30):
        raise ValueError("benchmark requires 1-30 matching completed units")
    by_id = {u["unit_id"]: u for u in units}
    rows = []
    for chapter in sorted(chapters, key=lambda c: c["number"]):
        unit_id = chapter["unit_id"]
        unit = by_id[unit_id]
        if (chapter["status"] != "reviewed" or unit["lifecycle"] != "reviewed"
                or unit["review"] != "pass" or unit["chapter_number"] != chapter["number"]
                or any(unit[k] != chapter[k] for k in ("source_path", "translation_path"))):
            raise ValueError("unit lacks current canonical PASS")
        source = inside(book, chapter["source_path"]).read_bytes()
        target = inside(book, chapter["translation_path"]).read_bytes()
        accepted = chapter["translation_acceptance"]
        if (accepted["source_sha256"] != sha(source)
                or accepted["translation_sha256"] != sha(target)):
            raise ValueError("accepted artifact bytes changed")
        rows.append({"unit_id": unit_id,
                     "source": {"text": source.decode("utf-8"), "sha256": sha(source)},
                     "candidate": {"text": target.decode("utf-8"), "sha256": sha(target),
                                   "source_sha256": sha(source)}})
    if clean_head(root) != commit:
        raise ValueError("state changed during export")
    return {"commit": commit, "workflow_revision": revision,
            "source_sha256": metadata["source"]["sha256"],
            "target_language": metadata["target_language"], "units": rows}


def paired(auto, chatops):
    for key in ("workflow_revision", "source_sha256", "target_language"):
        if auto[key] != chatops[key]:
            raise ValueError("paired snapshots differ in source, language or workflow")
    if len(auto["units"]) != len(chatops["units"]):
        raise ValueError("unit count mismatch")
    result = {"schema_version": 1, "target_language": auto["target_language"], "units": []}
    for left, right in zip(auto["units"], chatops["units"]):
        if left["unit_id"] != right["unit_id"] or left["source"] != right["source"]:
            raise ValueError("unit source mismatch")
        result["units"].append({"unit_id": left["unit_id"], "source": left["source"],
                                "auto": left["candidate"], "chatops": right["candidate"]})
    validate_manifest(result)
    return result


def canonical_preflight(root, slug, revision):
    # Use a disposable clone of the local public engine object store; never
    # install code into a state repository or run a mutable main checkout.
    with tempfile.TemporaryDirectory(prefix="blind-export-engine-") as tmp:
        engine = Path(tmp) / "engine"
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--local",
                        str(PUBLIC), str(engine)], check=True, capture_output=True, timeout=120)
        git(engine, "checkout", "--quiet", "--detach", revision)
        prepare(engine, root, revision)
        def run(script, *args):
            result = subprocess.run([sys.executable, str(engine / "scripts" / script), *args],
                                    cwd=engine, capture_output=True, text=True, timeout=200)
            if result.returncode:
                raise ValueError("pinned canonical preflight failed")
            return result.stdout
        run("book.py", "validate", slug)
        run("corpus.py", "verify", slug)
        return json.loads(run("book.py", "status", slug, "--json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto-root", type=Path, required=True)
    parser.add_argument("--chatops-root", type=Path, required=True)
    parser.add_argument("--auto-slug", required=True)
    parser.add_argument("--chatops-slug", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    a, b = args.auto_root.resolve(strict=True), args.chatops_root.resolve(strict=True)
    output = args.output.resolve()
    if a == b or a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError("two isolated state checkouts are required")
    if any(output.is_relative_to(p) for p in (a, b, PUBLIC)) or output.exists():
        raise ValueError("manifest must be a new file outside all checkouts")
    auto = checked_snapshot(a, args.auto_slug, canonical_preflight)
    chatops = checked_snapshot(b, args.chatops_slug, canonical_preflight)
    result = paired(auto, chatops)
    # The manifest contains the complete literary source and both targets.
    # Exclusive creation and mode 0600 protect it even under a permissive umask.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    # Only non-content provenance goes to stdout; labels are operator-supplied.
    print(json.dumps({"auto_commit": auto["commit"], "chatops_commit": chatops["commit"],
                      "workflow_revision": auto["workflow_revision"],
                      "source_sha256": auto["source_sha256"],
                      "unit_count": len(result["units"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
