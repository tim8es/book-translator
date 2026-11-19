"""Transactional correction safety. Never produces literary PASS evidence."""
from __future__ import annotations
import hashlib
import json
import os
import re
from correction_regression import CorrectionRegression
import candidate_review
import plain_review_scope
import correction_feedback

BLOCK = re.compile(r"<!--\s*bt-block:(\d+)\s*-->")
MARKUP = re.compile(r"<!--\s*bt-[^>]*-->")

def enabled(env=None):
    value=(os.environ if env is None else env).get("BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY","off")
    if value not in {"on","off"}:
        raise ValueError("BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY must be on or off")
    return value=="on"

def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def blocks(text):
    markers=list(BLOCK.finditer(text))
    if not markers or len({m.group(1) for m in markers})!=len(markers):
        return {}
    return {m.group(1):(m.end(),markers[i+1].start() if i+1<len(markers) else len(text))
            for i,m in enumerate(markers)}

def containing(ranges,start,end):
    found=[k for k,(lo,hi) in ranges.items() if lo<=start<=end<=hi]
    return found[0] if len(found)==1 else None

def visible_size(text):
    return len(re.sub(r"\s+","",MARKUP.sub("",text)))

def alarm(code,**proof):
    raise CorrectionRegression(code,proof)

def shortened(a,b):
    return a>=35 and a-b>=20 and b*100<a*65

def require_safe(source,before,after,patches,findings,position,*,force=False):
    if not (force or enabled()):
        return
    by_id={f["id"]:f for f in findings}
    left,old,new=blocks(source),blocks(before),blocks(after)
    aligned=bool(left) and list(left)==list(old)==list(new)
    affected={}
    for p in patches:
        f=by_id.get(p["finding_id"])
        if f is None:
            alarm("unbound-correction")
        replace=p["operation"]=="replace"
        replacement=p["replacement"]
        if not replacement.strip() and not (replace and f["category"]=="addition"):
            alarm("empty-non-addition")
        if correction_feedback.enabled() and replace and replacement==p["target_anchor"]["quote"]:
            alarm("unchanged-correction")
        if __import__("single_review_gate").enabled() and f["category"] not in {"omission","addition","structure"}:
            if __import__("single_review_gate").paragraph_count(p["target_anchor"]["quote"])!=__import__("single_review_gate").paragraph_count(replacement):
                alarm("unexpected-paragraph-boundary-change")
        st,en=position(before,p["target_anchor"])
        if replace and f["category"]!="addition":
            a,b=visible_size(before[st:en]),visible_size(replacement)
            if shortened(a,b):
                alarm("suspicious-anchor-content-loss",before_chars=a,after_chars=b)
        if aligned:
            ss,se=position(source,f["source_anchor"])
            sid,tid=containing(left,ss,se),containing(old,st,en)
            if sid is None or tid is None or sid!=tid:
                alarm("correction-block-scope-mismatch")
            affected.setdefault(tid,[]).append(f)
    if aligned:
        for k,fs in affected.items():
            a,b=visible_size(before[slice(*old[k])]),visible_size(after[slice(*new[k])])
            if shortened(a,b) and not all(f["category"]=="addition" for f in fs):
                alarm("suspicious-block-content-loss",block_id=k,before_chars=a,after_chars=b,
                      source_chars=visible_size(source[slice(*left[k])]))

def correction_context(source,translation,finding,position):
    if not enabled():
        return None
    left,right=blocks(source),blocks(translation)
    if plain_review_scope.plain(source,translation):
        try:
            ss,se=position(source,finding["source_anchor"])
            a,b=plain_review_scope.paragraph_window(source,ss,se)
            anchor=finding.get("translation_anchor")
            if anchor is None:
                return {"complete_source_paragraph":source[a:b]}
            ts,te=position(translation,anchor)
            c,d=plain_review_scope.paragraph_window(translation,ts,te)
            if b-a+d-c>10000:
                return None
            return {"complete_source_paragraph":source[a:b],
                    "complete_current_target_paragraph":translation[c:d],
                    "alignment":"verify reviewer-anchored correspondence"}
        except plain_review_scope.ScopeError:
            return None
    if not left or list(left)!=list(right):
        return None
    ss,se=position(source,finding["source_anchor"])
    bid=containing(left,ss,se)
    if bid is None:
        return None
    a,b=left[bid];c,d=right[bid]
    if b-a+d-c>10000:
        return None
    return {"block_id":bid,"complete_source_block":source[a:b],
            "complete_current_target_block":translation[c:d]}

def transaction_cases(source,before,after,patches,findings,position):
    """Verify actual combined per-block candidate, not independent patch excerpts."""
    left,old,new=blocks(source),blocks(before),blocks(after)
    by_id={f["id"]:f for f in findings}
    def claim(ids):
        return [{"id":i,"detail":by_id[i]["detail"],"category":by_id[i]["category"],
                 "severity":by_id[i]["severity"]} for i in ids]
    if plain_review_scope.plain(source,before) and plain_review_scope.plain(source,after):
        try:
            return plain_review_scope.candidate_cases(source,before,after,patches,findings,position)
        except (plain_review_scope.ScopeError,KeyError,TypeError) as exc:
            raise candidate_review.CandidateReviewError("unmarked correction scope unsafe: "+str(exc)) from exc
    if not left or list(left)!=list(old) or list(old)!=list(new):
        if len(source)+len(before)+len(after)>18000:
            raise candidate_review.CandidateReviewError("unaligned transaction exceeds safe full-context limit")
        ids=[p["finding_id"] for p in patches]
        return [{"finding_id":"transaction","patch_ids":ids,"reviewer_claim":claim(ids),
                 "original_excerpt":source,"current_translation_excerpt":before,
                 "candidate_translation_excerpt":after}]
    groups={}
    for p in patches:
        st,en=position(before,p["target_anchor"])
        bid=containing(old,st,en)
        if bid is None:
            raise candidate_review.CandidateReviewError("transaction patch lacks stable block scope")
        groups.setdefault(bid,[]).append(p["finding_id"])
    result=[]
    ids=list(left)
    for bid,fs in sorted(groups.items()):
        i=ids.index(bid)
        def paired(text,ranges):
            a,b=ranges[bid]
            return {"before_context":text[slice(*ranges[ids[i-1]])][-350:] if i else "",
                    "block":text[a:b],
                    "after_context":text[slice(*ranges[ids[i+1]])][:350] if i+1<len(ids) else ""}
        result.append({"finding_id":"block-"+bid,"patch_ids":fs,"reviewer_claim":claim(fs),
                       "exact_edits":[{"finding_id":p["finding_id"],"operation":p["operation"],
                                       "old_quote":p["target_anchor"]["quote"],"replacement":p["replacement"]}
                                      for p in patches if p["finding_id"] in fs],
                       "original_excerpt":paired(source,left),
                       "current_translation_excerpt":paired(before,old),
                       "candidate_translation_excerpt":paired(after,new)})
    return result

def group_prompts(cases,contract):
    """Bound each paid request by size, without omitting any case."""
    groups=[];current=[]
    def build(items):
        if correction_feedback.enabled():
            return correction_feedback.prompt(items,contract)
        system,user=candidate_review.build_prompt(items,contract,max_prompt_chars=52_000)
        user+=("\nCheck the actual COMBINED candidate for a source-owned block "
               "or an exact reviewer-anchor-linked paragraph window. Separate source excerpts are "
               "not assumed aligned; verify the mapping. Check all patch_ids together. "
               "Reject if ANY source-supported content was lost or newly distorted, "
               "if a claimed correction did not address its reviewed issue, or if uncertain. "
               "Return one decision per case finding_id, not per patch_ids member.")
        if len(system)+len(user)>52_000:
            raise candidate_review.CandidateReviewError("transaction group exceeds safe context")
        return system,user
    for case in cases:
        if len(current)>=8:
            groups.append((current,*build(current)));current=[]
        try:
            build(current+[case])
        except candidate_review.CandidateReviewError:
            if not current:
                raise
            groups.append((current,*build(current)));current=[]
            build([case])
        current.append(case)
    if current:
        groups.append((current,*build(current)))
    return groups

def group_key(group,system,user):
    return digest(json.dumps([group,system,user],ensure_ascii=False,sort_keys=True))


def approval_key(case,binding):
    """Exact candidate case plus every local policy/memory dependency."""
    return digest(json.dumps([binding,case],ensure_ascii=False,sort_keys=True))

def approval_cache(status,binding):
    prior=status.get("transactional_case_approvals")
    if not isinstance(prior,dict) or prior.get("binding")!=binding:
        prior={"binding":binding,"approved":[]}
    approved=prior.get("approved")
    if (not isinstance(approved,list) or len(approved)>300 or len(set(approved))!=len(approved)
            or any(not isinstance(x,str) or not re.fullmatch("[0-9a-f]{64}",x) for x in approved)):
        raise ValueError("transaction case approval cache invalid")
    status["transactional_case_approvals"]=prior
    return prior

def remember_approved(cache,cases,binding,rejected):
    approved=cache["approved"]
    for case in cases:
        if case["finding_id"] not in rejected:
            key=approval_key(case,binding)
            if key not in approved:
                approved.append(key)
    del approved[:-300]
