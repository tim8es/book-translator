"""Bounded first-review probe: one public-domain chapter, no repair loop.

A passing job means *measurement reached first independent review*, never that
the translation passed its literary gate or that EPUB was produced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from coherent_live_benchmark import run_arm
from quality_capacity_benchmark import CHAPTERS, REV, chapter_bytes, chapter_text, make_epub


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    from omniroute import config
    for role in ("TRANSLATOR","REVIEWER","CORRECTOR"):
        config(role)
    with tempfile.TemporaryDirectory(prefix="chapter-first-review-") as temporary:
        home=Path(temporary)
        chapter, blob=CHAPTERS[0]
        epub=home/"one-chapter.epub"
        fixtures=make_epub(epub,[(chapter,chapter_text(chapter_bytes(chapter,blob)))])
        print(json.dumps({"fixture":fixtures,"upstream_revision":REV}),flush=True)
        result=run_arm(home,epub.read_bytes(),"on",35,environment={
            "BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY":"on",
            "BOOK_TRANSLATOR_COMPACT_REVIEW":"on",
            "BOOK_TRANSLATOR_CORRECTION_FEEDBACK":"on",
            "BOOK_TRANSLATOR_SINGLE_REVIEW_GATE":"on",
            "BOOK_TRANSLATOR_PARAGRAPH_REVIEW":"on",
            "BOOK_TRANSLATOR_RESILIENT_RECOVERY":"on",
            "BOOK_TRANSLATOR_SPAN_MAP":"on",
            "BOOK_TRANSLATOR_OPTIONAL_MEMORY_ISOLATION":"on",
            "BOOK_TRANSLATOR_REUSE_CLOSED_REVIEW":"on",
            "BOOK_TRANSLATOR_ADDRESSED_EPUB":"on",
            "BOOK_TRANSLATOR_CORRECTION_LINEAGE_FEEDBACK":"on",
            "BOOK_TRANSLATOR_MAPPED_FULL_REVIEW":"on",
            "BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on",
        },wall_budget_seconds=1800,stop_after_phase="reviewer-completed")

    converged=result.get("review_convergence",[])
    reached=(result["stop_reason"]=="benchmark-reviewer-completed" and
             result["accepted_operations"].get("translated",0)==1 and
             len(converged)==1)
    payload={"schema_version":1,"fixture":fixtures,"upstream_revision":REV,
             "purpose":"first-pass-protocol-and-first-independent-review",
             "first_review_measured":reached,
             "first_review":converged[0] if converged else None,
             "literary_pass_claimed":False,"result":result}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload,indent=2),flush=True)
    return 0 if reached else 1


if __name__=="__main__":
    raise SystemExit(main())
