"""Exact paragraph windows for marker-free source and target artifacts.

A paragraph ID is local to one artifact. It does NOT assert cross-language
alignment. Source evidence comes from the independently reviewed anchors.
"""
from __future__ import annotations
import re

SEP=re.compile(r"\n[ \t]*\n")
BLOCK=re.compile(r"<!--\s*bt-block:")
MAX_PASSAGE=6500

class ScopeError(ValueError):
    pass

def plain(source,target):
    return not BLOCK.search(source) and not BLOCK.search(target)

def paragraphs(text,lo=0,hi=None):
    hi=len(text) if hi is None else hi
    cuts=[lo]+[m.end() for m in SEP.finditer(text,lo,hi) if lo<m.end()<hi]+[hi]
    return [(a,b) for a,b in zip(cuts,cuts[1:]) if a<b]

def addressed(text,core,prefix,*,max_ids=240):
    spans=paragraphs(text,*core)
    # Every character is owned, including blank separators. For extremely
    # fragmented cores use one explicit core ID instead of dropping text.
    if len(spans)>max_ids:
        spans=[tuple(core)]
    return {prefix+("%03d"%(i+1)):span for i,span in enumerate(spans)}

def paragraph_window(text,start,end):
    if not 0<=start<=end<=len(text):
        raise ScopeError("anchor outside artifact")
    spans=paragraphs(text)
    hits=[(a,b) for a,b in spans if a<end and start<b]
    if not hits:
        hits=[(a,b) for a,b in spans if a<=start<b or start==len(text)==b]
    if not hits:
        raise ScopeError("anchor has no paragraph")
    lo,hi=hits[0][0],hits[-1][1]
    if hi-lo>MAX_PASSAGE:
        raise ScopeError("anchored paragraph exceeds safe bounded passage")
    return lo,hi

def context(text,lo,hi):
    return {"before_context":text[max(0,lo-350):lo],
            "block":text[lo:hi],"after_context":text[hi:hi+350]}

def edit_intervals(before,patches,position):
    out=[]
    for p in patches:
        a,b=position(before,p["target_anchor"])
        if p["operation"]=="insert_before":
            b=a
        elif p["operation"]=="insert_after":
            a=b
        elif p["operation"]!="replace":
            raise ScopeError("unknown patch operation")
        out.append((a,b,p))
    out.sort(key=lambda e:(e[0],e[1]))
    cursor=0
    for a,b,p in out:
        if a<cursor:
            raise ScopeError("intersecting edit interval")
        cursor=b
    return out

def after_offset(edits,offset,*,right=False):
    shift=0
    for a,b,p in edits:
        if a==b==offset:
            if right:
                shift+=len(p["replacement"])
            continue
        if b<=offset:
            shift+=len(p["replacement"])-(b-a)
        elif a<offset<b:
            raise ScopeError("candidate scope cuts through edit")
    return offset+shift

def candidate_cases(source,before,after,patches,findings,position):
    edits=edit_intervals(before,patches,position)
    parts=[];cursor=0
    for a,b,p in edits:
        parts.extend((before[cursor:a],p["replacement"]))
        cursor=b
    parts.append(before[cursor:])
    if "".join(parts)!=after:
        raise ScopeError("candidate differs from exact staged edits")
    by_id={f["id"]:f for f in findings}
    groups=[]
    for a,b,p in edits:
        # Use the full original anchor, including at insertion boundaries.
        x,y=position(before,p["target_anchor"])
        lo,hi=paragraph_window(before,x,y)
        if groups and lo<groups[-1][1]:
            g=groups[-1]
            g[1]=max(g[1],hi);g[2].append(p)
        else:
            groups.append([lo,hi,[p]])
    # Each edit is owned by exactly one case. Adjacent windows are kept
    # separate, with the actual combined neighboring context shown.
    result=[]
    for i,(lo,hi,items) in enumerate(groups,1):
        if hi-lo>MAX_PASSAGE:
            # Keep a bounded scope rather than truncating a changed passage.
            # Adjacent (but not overlapping) paragraphs can be separated below.
            raise ScopeError("combined changed paragraph window exceeds safe bound")
        # Include only boundary insertions owned by this case. Other boundary
        # edits shift the interval but are not themselves part of this passage.
        own={p["finding_id"] for p in items}
        nl=after_offset(edits,lo)+sum(len(p["replacement"]) for a,b,p in edits
                                    if a==b==lo and p["finding_id"] not in own)
        nr=after_offset(edits,hi)+sum(len(p["replacement"]) for a,b,p in edits
                                    if a==b==hi and p["finding_id"] in own)
        if not 0<=nl<=nr<=len(after) or nr-nl>MAX_PASSAGE:
            raise ScopeError("candidate paragraph window exceeds safe bound")
        claims=[];sources={}
        for p in items:
            f=by_id.get(p["finding_id"])
            if f is None:
                raise ScopeError("candidate contains unknown finding")
            ss,se=position(source,f["source_anchor"])
            sl,sh=paragraph_window(source,ss,se)
            key=(sl,sh)
            if key not in sources:
                sources[key]={"source":context(source,sl,sh),"finding_ids":[]}
            sources[key]["finding_ids"].append(f["id"])
            claims.append({"id":f["id"],"detail":f["detail"],"category":f["category"],
                           "severity":f["severity"],"source_quote":f["source_anchor"]["quote"]})
        result.append({"finding_id":"paragraph-%d-%d"%(lo,hi),"patch_ids":[p["finding_id"] for p in items],
                       "exact_edits":[{"finding_id":p["finding_id"],"operation":p["operation"],
                                       "old_quote":p["target_anchor"]["quote"],"replacement":p["replacement"]}
                                      for p in items],
                       "reviewer_claim":claims,"original_excerpt":{"anchor_linked_passages":list(sources.values()),
                           "alignment":"source and target correspondence must be verified, not assumed"},
                       "current_translation_excerpt":context(before,lo,hi),
                       "candidate_translation_excerpt":context(after,nl,nr)})
        # Cases hash exact content, including neighboring candidate context.
    seen=[fid for case in result for fid in case["patch_ids"]]
    if len(seen)!=len(set(seen)) or set(seen)!={p["finding_id"] for p in patches}:
        raise ScopeError("candidate scope lacks exact patch coverage")
    return result
