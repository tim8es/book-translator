"""Independent, opt-in Markdown-bound XHTML preview commands.

Never mutate books/<slug> lifecycle state, source-coverage or output manifests.
The new commands are staging tools for migration and preview validation only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .epub_preserve_markdown import (
    MANIFEST_NAME,
    EpubMarkedMarkdownError,
    json_manifest,
    make_markdown_bundle,
    preview_from_markdown,
)


class EpubPreserveCliError(ValueError):
    pass


def export_command(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    if not source.is_file():
        raise EpubPreserveCliError("original source EPUB is not readable")
    if output_dir.exists():
        raise EpubPreserveCliError("output directory already exists; refusing overwrite")
    original = source.read_bytes()
    try:
        manifest, markdown = make_markdown_bundle(original)
    except EpubMarkedMarkdownError as exc:
        raise EpubPreserveCliError(str(exc)) from exc
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / MANIFEST_NAME).write_bytes(json_manifest(manifest))
    for filename, content in markdown.items():
        (output_dir / filename).write_text(content, encoding="utf-8", newline="\n")
    print(f"XHTML/Markdown staging exported: {len(markdown)} units to {output_dir}")
    print("Only change prose between BT:BLOCK / BT:END markers; preserve all BT tokens.")
    return 0


def preview_command(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser()
    directory = Path(args.translations_dir).expanduser()
    target = Path(args.output).expanduser()
    if not source.is_file() or not directory.is_dir():
        raise EpubPreserveCliError("original EPUB and translation directory must exist")
    if not target.name.endswith("_PREVIEW.epub"):
        raise EpubPreserveCliError("output name must end with _PREVIEW.epub (not a final build)")
    if target.exists():
        raise EpubPreserveCliError("output EPUB already exists; refusing overwrite")
    try:
        manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EpubPreserveCliError(f"cannot read exact-source Markdown manifest: {exc}") from exc
    expected = {doc["filename"] for doc in manifest.get("documents", [])} if isinstance(manifest, dict) else set()
    if any(not isinstance(name, str) or "/" in name or "\\" in name for name in expected):
        raise EpubPreserveCliError("unsafe translation path in Markdown manifest")
    present = {p.name for p in directory.iterdir() if p.suffix == ".md"}
    if present != expected:
        raise EpubPreserveCliError("translation directory has missing or extra Markdown documents")
    documents = {}
    for name in sorted(expected):
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise EpubPreserveCliError("translation must be a regular Markdown file")
        try:
            documents[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise EpubPreserveCliError(f"cannot read Markdown translation {name}: {exc}") from exc
    try:
        preview = preview_from_markdown(
            source.read_bytes(), manifest, documents,
            language=args.language, title=args.title,
        )
    except EpubMarkedMarkdownError as exc:
        raise EpubPreserveCliError(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as file:
        file.write(preview)
    print(f"Created structural PREVIEW EPUB: {target} ({len(preview)} bytes)")
    print("This does not verify literary translation quality or promote reviewed state.")
    return 0


def register_preserve_commands(subparsers: argparse._SubParsersAction, error_factory):
    export = subparsers.add_parser(
        "epub-preserve-export",
        help="Export EPUB XHTML to source-bound, editable marked Markdown staging.",
    )
    export.add_argument("source", help="Exact English/source EPUB")
    export.add_argument("--output-dir", required=True)
    export.set_defaults(func=_adapt(export_command, error_factory))

    preview = subparsers.add_parser(
        "epub-preserve-preview",
        help="Assemble a structural PREVIEW from exact original and marked Markdown translations.",
    )
    preview.add_argument("source", help="Exactly the EPUB used for exporting Markdown")
    preview.add_argument("--translations-dir", required=True)
    preview.add_argument("--language", required=True)
    preview.add_argument("--title", help="Localized book title (optional)")
    preview.add_argument("--output", required=True, help="Output ending _PREVIEW.epub")
    preview.set_defaults(func=_adapt(preview_command, error_factory))


def _adapt(handler, error_factory):
    def run(args):
        try:
            return handler(args)
        except EpubPreserveCliError as exc:
            raise error_factory(str(exc)) from exc
    return run
