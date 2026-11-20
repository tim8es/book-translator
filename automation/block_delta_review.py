"""Conservative source-paired follow-up review, not acceptance evidence."""
from __future__ import annotations
from transactional_quality import blocks, containing, digest

CHECKS={"coverage","meaning","terminology","style","structure"}
NONLOCAL={"structure","voice"}

def scope(source,before,current,previous,shared,position):
    report=previous.get("quality_report",{})
    if (previous.get("outcome")!="CORRECTIONS_REQUIRED"
            or report.get("evidence_version")!=2 or report.get("context")!="independent_session"
            or report.get("checks")!={k:True for k in CHECKS}
            or previous.get("shared_state_revisions")!=shared
            or previous.get("source_sha256")!=digest(source)
            or previous.get("translation_sha256")!=digest(before)):
        return None
    left,old,new=blocks(source),blocks(before),blocks(current)
    if not left or list(left)!=list(old) or list(old)!=list(new):
        return None
    ids=list(left)
    # Not just counts: exact unchanged source-owned block bodies and prefix.
    if before[:next(iter(old.values()))[0]]!=current[:next(iter(new.values()))[0]]:
        return None
    changed={k for k in ids if before[slice(*old[k])]!=current[slice(*new[k])]}
    if not changed or len(changed)*4>len(ids):
        return None
    open_findings=[f for f in report.get("findings",[]) if not f["resolved"]]
    if not open_findings:
        return None
    required=set(changed); recheck=[]
    for f in open_findings:
        if f["category"] in NONLOCAL or (f["severity"]!="minor" and f["category"] in
                                         {"referent","register","style"}):
            return None
        ss,se=position(source,f["source_anchor"])
        bid=containing(left,ss,se)
        if bid is None:
            return None
        required.add(bid)
        anchor=f["translation_anchor"]
        if anchor:
            ts,te=position(before,anchor)
            target=containing(old,ts,te)
            if target!=bid:
                return None
        recheck.append({"finding":f,"source_block_id":bid})
    # Include adjacent blocks explicitly to account for local continuity. If
    # the dependency set is large, ordinary full independent review wins.
    expanded=set(required)
    for k in required:
        i=ids.index(k)
        expanded.update(ids[max(0,i-1):i+2])
    if len(expanded)*2>len(ids):
        return None
    pairs=[{"block_id":k,"source":source[slice(*left[k])],
            "previous_target":before[slice(*old[k])],
            "current_target":current[slice(*new[k])],
            "changed":k in changed}
           for k in ids if k in expanded]
    return {"block_delta_version":1,"previous_record_id":previous["record_id"],
            "previous_source_sha256":digest(source),"previous_translation_sha256":digest(before),
            "current_translation_sha256":digest(current),"previous_checks":report["checks"],
            "previous_findings":report["findings"],"unresolved_findings":recheck,
            "changed_translation_sections":pairs,"changed_block_ids":sorted(changed),
            "scope_block_ids":[k for k in ids if k in expanded],
            "unchanged_outside_scope_count":len(ids)-len(expanded)}
