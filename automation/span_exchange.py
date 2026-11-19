#!/usr/bin/env python3
"""Shared, deterministic span exchange for human-led and automatic execution.

The CLI emits only read-only, exact source/target packets. It never calls a
model or changes a claim, ledger, glossary, translation, or book pin.
The existing ChatOps bridge can pass structural segment output and addressed
review responses through this same compiler and the pinned canonical CLI.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import addressed_review
import review_chunks
import span_map

MAX_PARTS=review_chunks.MAX_CHUNKS


def translation_map(source,translation,segments,helper,plan_builder):
    """Do not accept a caller-supplied correspondence map or arbitrary cuts."""
    if (not isinstance(segments,dict) or not segments or len(segments)>20000
            or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip()
                   for k,v in segments.items())):
        raise ValueError("invalid structural segment response")
    plan=plan_builder(source,helper)
    if helper.render(plan,segments)!=translation:
        raise ValueError("segment rendering does not reproduce submitted translation")
    return span_map.from_render(plan,segments,source,translation)


def correction_map(source,before,candidate,mapping,record,acceptance,intents,helper):
    """Verify a correction against the same current evidence and patch rules as Auto.

    The returned map describes only actual applied bytes. It is not review
    evidence or permission to promote the corrected translation to reviewed.
    """
    import book_runner as runner
    import transactional_quality
    import correction_regression
    if (not isinstance(record,dict) or record.get("outcome")!="CORRECTIONS_REQUIRED"
            or not isinstance(acceptance,dict)
            or record.get("source_sha256")!=span_map.sha(source)
            or record.get("translation_sha256")!=span_map.sha(before)
            or acceptance.get("source_sha256")!=record["source_sha256"]
            or acceptance.get("translation_sha256")!=record["translation_sha256"]):
        raise ValueError("correction requires current accepted rejected version")
    report=record.get("quality_report")
    if not isinstance(report,dict) or report.get("evidence_version")!=2:
        raise ValueError("correction requires anchored canonical review")
    findings=[f for f in report["findings"] if not f["resolved"]]
    if not findings:
        raise ValueError("no current unresolved correction finding")
    # Same parser and versioned protocol as the Auto runner. Partial progress
    # is allowed but all unaddressed issues remain for canonical re-review.
    patches,_,_=runner.parse_correction_patches(json.dumps(
        {"patches":intents,"glossary":[],"style_guide":[]},ensure_ascii=False),
        findings,allow_partial=True)
    by_id={f["id"]:f for f in findings}
    for p in patches:
        helper.validate_intent(p,by_id[p["finding_id"]])
        if p["operation"]=="replace" and p["replacement"]==p["target_anchor"]["quote"]:
            raise ValueError("unchanged correction")
    after=runner.apply_correction_patches(before,patches)
    if after==before or after!=candidate:
        raise ValueError("exact patches do not reproduce submitted corrected text")
    runner.check_markers(source,after)
    transactional_quality.require_safe(source,before,after,patches,findings,runner._anchor_position,force=True)
    correction_regression.require_no_new_repetitions(before,after)
    if mapping is None:
        return None
    return span_map.after_patches(mapping,source,before,after,patches,runner._anchor_position)


def parts(source,target,mapping):
    # Match the Auto mapped review planner. Single-part human-led review is
    # valid for small units; it still covers all original and accepted text.
    import book_runner
    if (book_runner.EPUB_MARKER_RE.search(source) or book_runner.MARKDOWN_LINK_RE.search(source)
            or book_runner.EPUB_MARKER_RE.search(target) or book_runner.MARKDOWN_LINK_RE.search(target)):
        raise ValueError("addressed exchange currently supports plain prose only")
    groups=span_map.groups(mapping,source,target,review_chunks.TARGET_CHARS,review_chunks._window)
    if groups is None or not 1<=len(groups)<=MAX_PARTS:
        raise ValueError("cannot safely partition addressed review")
    return groups


def prior_for(previous,part,source):
    if previous is not None and previous.get("source_sha256")!=span_map.sha(source):
        # A different source edition cannot supply current prior findings.
        previous=None
    return review_chunks.find_previous_for_chunk(previous,part,source)


def prepare(source,target,mapping,previous=None):
    result=[]
    for part in parts(source,target,mapping):
        packet,sr,_=addressed_review.packet(source,target,part,mapping)
        prior=prior_for(previous,part,source)
        old=[]
        for f in prior:
            q=f["source_anchor"];start=review_chunks._evidence_offset(source,q)
            ids=[k for k,(a,b) in sr.items() if a<start+len(q["quote"]) and start<b]
            if not ids:raise ValueError("prior finding outside addressed source packet")
            old.append({"id":f["id"],"category":f["category"],"severity":f["severity"],
                        "detail":f["detail"],"source_ids":ids})
        result.append({"chunk_id":part["id"],"packet":packet,"prior_findings":old})
    return {"schema_version":1,"source_sha256":span_map.sha(source),
            "translation_sha256":span_map.sha(target),"parts":result}


def compile_review(source,target,mapping,previous,responses,outcome):
    """Compile every independent part in deterministic order, never invent PASS.

    Caller must enforce a live independent Reviewer claim and then submit
    the resulting ordinary v2 report to the pinned review-record validator.
    """
    if outcome not in {"PASS","CORRECTIONS_REQUIRED"}:
        raise ValueError("invalid requested review outcome")
    chunks=parts(source,target,mapping)
    if not isinstance(responses,list) or len(responses)!=len(chunks):
        raise ValueError("addressed review needs every assigned part")
    by_id={}
    for row in responses:
        if (not isinstance(row,dict) or set(row)!={"chunk_id","response"}
                or not isinstance(row["chunk_id"],str) or row["chunk_id"] in by_id
                or not isinstance(row["response"],dict)):
            raise ValueError("invalid or repeated addressed part")
        by_id[row["chunk_id"]]=row["response"]
    if set(by_id)!={c["id"] for c in chunks}:
        raise ValueError("addressed review parts do not match current plan")
    stage={"plan":{"chunks":chunks},"completed":{}}
    for chunk in chunks:
        response=by_id[chunk["id"]]
        # ChatOps review-record does not change book-wide literary memory.
        # Reconcile proposals separately using the existing pinned CAS path.
        if response.get("glossary")!=[] or response.get("style_guide")!=[]:
            raise ValueError("reconcile ChatOps literary memory proposals separately")
        prior=prior_for(previous,chunk,source)
        raw=json.loads(addressed_review.compile_response(json.dumps(response,ensure_ascii=False),
                       source,target,chunk,mapping,prior))
        report={"evidence_version":2,"context":"independent_session",
                "checks":raw["checks"],"findings":raw["findings"]}
        review_chunks.validate_chunk_report(chunk,report,source,target)
        stage["completed"][chunk["id"]]={"outcome":raw["outcome"],"report":report,
                                          "glossary":[],"style":[]}
    combined=review_chunks.combine(stage)
    if combined["outcome"]!=outcome:
        raise ValueError("combined independent outcome differs from request")
    return {"evidence_version":2,"context":"independent_session",
            "checks":combined["checks"],"findings":combined["findings"]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="operation",required=True)
    translation=sub.add_parser("translation-plan")
    translation.add_argument("--source",type=Path,required=True)
    translation.add_argument("--engine",type=Path,required=True)
    review=sub.add_parser("review-plan")
    for name in ("source","translation","map"):
        review.add_argument("--"+name,type=Path,required=True)
    review.add_argument("--previous",type=Path,help="latest canonical review record, if present")
    args=parser.parse_args(argv)
    source=args.source.read_bytes().decode("utf-8")
    if args.operation=="translation-plan":
        import book_runner
        helper=book_runner.load_public_structural_helper(args.engine)
        plan=book_runner.bounded_source_plan(source,helper)
        result={"schema_version":1,"source_sha256":span_map.sha(source),"segments":helper.spans(plan)}
    else:
        target=args.translation.read_bytes().decode("utf-8")
        mapping=json.loads(args.map.read_bytes().decode("utf-8"))
        previous=json.loads(args.previous.read_bytes().decode("utf-8")) if args.previous else None
        result=prepare(source,target,mapping,previous)
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=="__main__":raise SystemExit(main())
