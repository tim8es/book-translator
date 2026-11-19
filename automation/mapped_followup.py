"""Conservative, exact-byte mapped follow-up under the pinned v2 contract.

No approval is inferred here. A bounded review must be independently invoked
on the currently accepted artifact. This only establishes what may be shown
under the canonical focused_candidate policy. A new full review is compulsory
when the dependency scope, literary memory or source identity is uncertain.
"""
import addressed_review
import span_map

CHECKS={"coverage","meaning","terminology","style","structure"}
NONLOCAL={"structure","voice","terminology"}

def scope(s,before,current,old_map,new_map,previous,shared,delta,position):
    report=previous.get("quality_report",{})
    if (delta.get("mode")!="focused_candidate" or delta.get("previous_record_id")!=previous.get("record_id")
            or delta.get("source_sha256")!=span_map.sha(s) or delta.get("translation_sha256")!=span_map.sha(current)
            or previous.get("outcome")!="CORRECTIONS_REQUIRED"
            or report.get("evidence_version")!=2 or report.get("context")!="independent_session"
            or report.get("checks")!=dict.fromkeys(CHECKS,True)
            or previous.get("source_sha256")!=span_map.sha(s)
            or previous.get("translation_sha256")!=span_map.sha(before)
            or previous.get("shared_state_revisions")!=shared):
        return None
    span_map.validate(old_map,s,before);span_map.validate(new_map,s,current)
    old=old_map["spans"];new=new_map["spans"]
    if [(r["id"],r["source"],r["source_sha256"]) for r in old]!=[(r["id"],r["source"],r["source_sha256"]) for r in new]:
        return None
    # Compare actual full span bytes (not ratios or model-proposed addresses).
    changed={i for i,(a,b) in enumerate(zip(old,new))
             if before[slice(*a["target"])]!=current[slice(*b["target"])]}
    if not changed:return None
    selected=set(changed);prior=[]
    for f in report.get("findings",[]):
        if f["resolved"]:continue
        if (f["category"] in NONLOCAL or f["severity"]=="critical"
                or (f["severity"]!="minor" and f["category"] in {"style","register","referent"})):
            return None
        a,b=position(s,f["source_anchor"])
        owners=[i for i,r in enumerate(old) if r["source"][0]<=a<b<=r["source"][1]]
        if len(owners)!=1:return None
        i=owners[0]
        if f["translation_anchor"] is not None:
            a,b=position(before,f["translation_anchor"])
            x,y=old[i]["target"]
            if not x<=a<b<=y:return None
        selected.add(i)
        prior.append({**f,"previous_record_id":previous["record_id"]})
    if not prior:return None
    # Include adjacent whole spans in every semantic scope, never truncate.
    expanded=set(selected)
    for i in selected:expanded.update(range(max(0,i-1),min(len(new),i+2)))
    if sum(new[i]["source"][1]-new[i]["source"][0] for i in expanded)*2>len(s):
        return None
    data={"previous_record_id":previous["record_id"],"previous_checks":report["checks"],
          "changed_spans":[new[i]["id"] for i in sorted(changed)],
          "unchanged_outside_scope":len(new)-len(expanded),"pairs":[]}
    sr={};tr={}
    for i in sorted(expanded):
        sc,tc=new[i]["source"],new[i]["target"]
        c={"source_core":sc,"translation_core":tc,"source_window":sc,"translation_window":tc}
        packet,ss,tt=addressed_review.packet(s,current,c,new_map)
        row=packet["pairs"][0]
        row["previous_target"]=before[slice(*old[i]["target"])]
        row["changed"]=i in changed
        data["pairs"].append(row);sr.update(ss);tr.update(tt)
    # The complete chapter was reviewed on the immediately preceding full
    # version. Unchanged outside bytes retain that coverage, not a new PASS.
    # Supply opening/closing context and force a full review if the new edit
    # could change nonlocal meaning or continuity.
    data["chapter_boundary_context"]={"source_open":s[:600],"source_close":s[-600:],
        "target_open":current[:600],"target_close":current[-600:]}
    return data,sr,tr,prior
