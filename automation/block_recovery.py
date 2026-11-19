"""Alternative correction on complete block, compiling only the anchored edit."""
import json
import re
from transactional_quality import blocks, containing
import plain_review_scope
import correction_feedback

class RecoveryError(ValueError):
    pass

def spec(source,target,finding,position):
    if finding.get("translation_anchor") is None:
        return None
    left,right=blocks(source),blocks(target)
    ss,se=position(source,finding["source_anchor"])
    ts,te=position(target,finding["translation_anchor"])
    if correction_feedback.enabled() and plain_review_scope.plain(source,target):
        try:
            sl,sh=plain_review_scope.paragraph_window(source,ss,se)
            a,b=plain_review_scope.paragraph_window(target,ts,te)
        except plain_review_scope.ScopeError:
            return None
        original=source[sl:sh]
    else:
        if not left or list(left)!=list(right):
            return None
        bid=containing(left,ss,se)
        if bid is None or bid!=containing(right,ts,te):
            return None
        a,b=right[bid]
        original=source[slice(*left[bid])]
    text=target[a:b]
    if len(original)+len(text)>8000 or re.search(r"<!--|\]\(",text):
        return None
    return {"finding_id":finding["id"],"source":original,"current_block":text,
            "prefix":target[a:ts],"replace_exactly":target[ts:te],"suffix":target[te:b]}

def prompt(packet):
    return ("\nALTERNATE CORRECTION STRATEGY: local patch generation previously failed. "
            "Reconstruct the entire assigned target block by comparing it to the original. "
            "Every character outside the exact reviewer-selected replacement must remain byte-identical. "
            "Return ONLY JSON with keys finding_id,candidate_block,glossary,style_guide. "
            "candidate_block is prefix + complete corrected replacement + suffix, including whitespace. "
            "The program extracts only the existing reviewer-authorized span; no new coordinates. "
            "No whole chapter rewrite. Retain all source-supported content. Exact data:\n"+
            json.dumps(packet,ensure_ascii=False))

def compile_response(answer,packet):
    try:
        raw=json.loads(answer)
    except (TypeError,ValueError) as exc:
        raise RecoveryError("invalid recovery JSON") from exc
    if not isinstance(raw,dict) or set(raw)!={"finding_id","candidate_block","glossary","style_guide"}:
        raise RecoveryError("invalid recovery fields")
    text=raw["candidate_block"]
    prefix,suffix=packet["prefix"],packet["suffix"]
    if (raw["finding_id"]!=packet["finding_id"] or not isinstance(text,str)
            or len(text)<len(prefix)+len(suffix) or not text.startswith(prefix)
            or not text.endswith(suffix)):
        raise RecoveryError("candidate changed text outside reviewer anchor")
    replacement=text[len(prefix):len(text)-len(suffix) if suffix else len(text)]
    if replacement==packet["replace_exactly"]:
        raise RecoveryError("recovery did not change the reviewed span")
    return json.dumps({"patches":[{"finding_id":packet["finding_id"],
                      "operation":"replace","replacement":replacement}],
                       "glossary":raw["glossary"],"style_guide":raw["style_guide"]},ensure_ascii=False)
