"""Source-owned correction groups. Canonical finding IDs and anchors stay intact.

Selection and prompt packing are deterministic, never permit a larger write
than the existing reviewer anchor, and never resolve a finding. A group
preflight compares the *combined* edited span with actual source and neighbors.
Unknown/ambiguous/cross-span evidence falls back to the existing safe path.
"""
from __future__ import annotations
import span_map

MAX_FINDINGS=12
MAX_CONTEXT=20000
NEIGHBOR=350


def owner(mapping,side,a,b):
    matches=[i for i,r in enumerate(mapping["spans"]) if r[side][0]<=a<b<=r[side][1]]
    return matches[0] if len(matches)==1 else None


def locate(mapping,source,target,f,position):
    if not f.get("translation_anchor"):return None
    ss,se=position(source,f["source_anchor"])
    ts,te=position(target,f["translation_anchor"])
    a=owner(mapping,"source",ss,se)
    b=owner(mapping,"target",ts,te)
    return (a,ts,te) if a is not None and a==b else None


def _passage(text,interval):
    a,b=interval
    return {"before_context":text[max(0,a-NEIGHBOR):a],
            "block":text[a:b],"after_context":text[b:b+NEIGHBOR]}


def packet(mapping,source,target,findings,position):
    span_map.validate(mapping,source,target)
    groups={}
    for f in findings:
        found=locate(mapping,source,target,f,position)
        if found is None:return None
        idx,_,_=found
        groups.setdefault(idx,[]).append(f)
    result=[]
    for idx,items in sorted(groups.items()):
        row=mapping["spans"][idx]
        result.append({"span_id":row["id"],"source":_passage(source,row["source"]),
                       "current_target":_passage(target,row["target"]),"findings":items})
    if sum(len(g["source"]["block"])+len(g["current_target"]["block"]) for g in result)>MAX_CONTEXT:
        return None
    return result


def selection(mapping,source,target,pending,baseline,staged,rejected,unit,record,position,
              *,failures=0,pressure=0,isolated=False):
    """Batch independent/disjoint edits, never pretend overlapping edits resolve each other."""
    if (mapping is None or not pending or failures or pressure or isolated):
        return baseline
    span_map.validate(mapping,source,target)
    unsafe={x["finding_id"] for x in rejected if x.get("unit_id")==unit and x.get("review_record_id")==record}
    def eligible(f):
        if f["id"] in unsafe or f["severity"]=="critical" or f["category"]=="structure":
            return None
        return locate(mapping,source,target,f,position)
    first=eligible(pending[0])
    if first is None:return baseline
    locked=[]
    for patch in staged:
        a,b=position(target,patch["target_anchor"])
        locked.append((a,b))
    candidates=[]
    for index,f in enumerate(pending):
        found=eligible(f)
        if found is not None:
            candidates.append((index,f,found))
    # Work on every eligible issue in the first source-owned passage before
    # moving to other passages; keep original relative order in each group.
    candidates.sort(key=lambda x:(x[2][0]!=first[0],x[2][0],x[0]))
    chosen=[];occupied=list(locked);owners=set();chars=0
    for _,f,(idx,a,b) in candidates:
        if any(a<d and c<b for c,d in occupied):
            continue
        row=mapping["spans"][idx]
        size=(row["source"][1]-row["source"][0]+row["target"][1]-row["target"][0]) if idx not in owners else 0
        if chars+size>MAX_CONTEXT:
            continue
        chosen.append(f);occupied.append((a,b));owners.add(idx);chars+=size
        if len(chosen)>=MAX_FINDINGS:break
    # Do not reorder past an overlapping first pending item indefinitely.
    if not chosen or pending[0]["id"] not in {f["id"] for f in chosen}:
        return baseline
    return chosen if len(chosen)>=len(baseline) else baseline


def cases(mapping,source,before,after,patches,findings,position):
    """Combined semantic-veto cases with exact source-owned passages.

    All applied edits and findings must have the same exact source/target
    ownership. For a null anchor or moved correspondence use the old path.
    """
    if mapping is None:return None
    span_map.validate(mapping,source,before)
    newer=span_map.after_patches(mapping,source,before,after,patches,position)
    if newer is None:return None
    by_id={f["id"]:f for f in findings};groups={}
    for p in patches:
        f=by_id[p["finding_id"]];found=locate(mapping,source,before,f,position)
        if found is None:return None
        i,a,b=found
        if p["target_anchor"]!=f["translation_anchor"]:return None
        groups.setdefault(i,[]).append(p)
    result=[]
    for i,ps in sorted(groups.items()):
        row=mapping["spans"][i];new=newer["spans"][i]
        ids=[p["finding_id"] for p in ps]
        result.append({"finding_id":"mapped-"+row["id"],"patch_ids":ids,
                       "reviewer_claim":[{k:by_id[fid][k] for k in ("id","detail","category","severity")} for fid in ids],
                       "exact_edits":[{"finding_id":p["finding_id"],"operation":p["operation"],
                                       "old_quote":p["target_anchor"]["quote"],"replacement":p["replacement"]} for p in ps],
                       "original_excerpt":_passage(source,row["source"]),
                       "current_translation_excerpt":_passage(before,row["target"]),
                       "candidate_translation_excerpt":_passage(after,new["target"])})
    return result
