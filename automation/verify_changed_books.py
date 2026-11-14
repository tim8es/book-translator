"""Fast read-only checks for files changed under books/ in GitHub Actions.

Literary correctness is NOT assessed here; canonical Translator/Reviewer
acceptance and hash-bound PASS evidence remain separate quality gates.
"""
import json
import os
import re
import tempfile
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(os.environ.get("BOOK_TRANSLATOR_STATE_ROOT", os.getcwd())).resolve()
TEXT_SUFFIXES = {".md", ".txt", ".json", ".html", ".xhtml", ".xml", ".css", ".yml", ".yaml"}
MAX_EPUB_EXPANDED_BYTES = 256 * 1024 * 1024


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _validation_engine(root, book, slug):
    """Run each book's exact canonical pin, not a stale private code copy.

    Build a read-only symlink to the state checkout inside an isolated public
    runtime. Never rewrite source books, acceptance records or book metadata.
    """
    try:
        metadata = json.loads((book / "metadata.json").read_text("utf-8"))
        revision = metadata["workflow"]["resolved_revision"]
        if not isinstance(revision, str) or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
            raise ValueError("book workflow lacks an immutable revision")
        bridge_file = book / ".workflow/workflow-upgrade-evidence.json"
        if bridge_file.is_file():
            bridge = json.loads(bridge_file.read_text("utf-8"))
            if bridge["target_workflow_revision"] != revision:
                raise ValueError("upgrade evidence does not match pinned workflow")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid pinned book workflow provenance: {exc}") from exc
    temporary = tempfile.TemporaryDirectory(prefix="book-pinned-validate-")
    engine = Path(temporary.name) / "engine"
    try:
        clone = subprocess.run(
            ["git", "clone", "--quiet", "https://github.com/tim8es/book-translator.git",
             str(engine)],
            capture_output=True, text=True, timeout=180,
        )
        if clone.returncode:
            raise RuntimeError("cannot fetch public canonical workflow for validation")
        checkout = subprocess.run(
            ["git", "-C", str(engine), "checkout", "--quiet", "--detach", revision],
            capture_output=True, text=True, timeout=45,
        )
        if checkout.returncode:
            raise RuntimeError("the pinned public workflow revision is unavailable")
        (engine / "books").mkdir(exist_ok=True)
        (engine / "books" / slug).symlink_to(book.resolve(), target_is_directory=True)
        (engine / ".book-translator-install.json").write_text(json.dumps({
            "schema_version": 1,
            "canonical_repository": "https://github.com/tim8es/book-translator",
            "requested_ref": revision,
            "resolved_revision": revision,
            "install_root": ".",
        }), encoding="utf-8")
    except Exception:
        temporary.cleanup()
        raise
    return engine, temporary


def verify_changed_books(paths, root=ROOT, run_canonical=True):
    """Return a list of errors; never write into books/."""
    root = Path(root)
    errors = []
    affected = set()
    for raw in paths:
        p = Path(raw)
        if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "books":
            errors.append(f"Unsafe changed path: {raw}")
            continue
        if len(p.parts) < 3 or p.parts[1].startswith("."):
            continue
        affected.add(p.parts[1])
        path = root / p
        if not path.exists():
            continue  # Deletions are checked through canonical book validation.
        if not path.is_file() or path.is_symlink():
            errors.append(f"Expected a regular book file: {raw}")
            continue
        try:
            if path.suffix.lower() in TEXT_SUFFIXES:
                data = path.read_text(encoding="utf-8")
                if path.suffix.lower() == ".json":
                    json.loads(
                        data, object_pairs_hook=_unique_keys,
                        parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Invalid JSON constant: {x}")),
                    )
            elif path.suffix.lower() == ".epub":
                with zipfile.ZipFile(path) as archive:
                    total = sum(item.file_size for item in archive.infolist())
                    if total > MAX_EPUB_EXPANDED_BYTES:
                        raise ValueError("EPUB expands beyond the safety limit")
                    bad = archive.testzip()
                    if bad:
                        raise ValueError(f"Corrupt EPUB member: {bad}")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, zipfile.BadZipFile,
                RuntimeError) as exc:
            errors.append(f"{raw}: {exc}")

    if run_canonical:
        for slug in sorted(affected):
            book = root / "books" / slug
            if not book.is_dir():
                continue
            if not (book / "source-revisions.json").is_file():
                print(f"NOTICE: books/{slug} is legacy or incomplete; file checks only. "
                      "Literary quality and machine PASS evidence were not verified.")
                continue
            try:
                engine, temporary = _validation_engine(root, book, slug)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.append(f"books/{slug}: {exc}")
                continue
            try:
                for command in (
                    [sys.executable, "scripts/book.py", "validate", slug],
                    [sys.executable, "scripts/corpus.py", "verify", slug],
                ):
                    result = subprocess.run(
                        command, cwd=engine, capture_output=True, text=True, timeout=180,
                    )
                    if result.returncode:
                        errors.append(
                            f"books/{slug}: {' '.join(command[1:])} failed: "
                            f"{(result.stdout + result.stderr)[-2000:]}"
                        )
                    else:
                        print(f"PASS: books/{slug} {' '.join(command[1:])}")
            finally:
                if temporary is not None:
                    temporary.cleanup()
    return errors


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_changed_books.py <changed-book-paths.json>")
    changed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not isinstance(changed, list) or any(not isinstance(item, str) for item in changed):
        raise SystemExit("Expected a JSON list of changed file paths")
    issues = verify_changed_books(changed)
    if issues:
        for issue in issues:
            print("ERROR:", issue, file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated changed book files: {len(changed)}")


if __name__ == "__main__":
    main()
