"""Deterministic passage IDs compiled into pinned v2 exact anchors."""
import json
import re
import compact_review
import span_map

PROTECTED=re.compile(r"<!--|\]\(|\n[ \t]*\n")
SENTENCE=re.compile(r'(?<=[.!?…])["»”’\)]*[ \t]+|\n+')

def intervals(text,lo,hi):
    cuts=[lo]+[lo+m.end() for m in SENTENCE.finditer(text[lo:hi]) if m.end()<hi-lo]+[hi]
    out=[]
    for a,b in zip(cuts,cuts[1:]):
        while b-a>220:
            end=a+220
            spaces=[m.end() for m in re.finditer(r"[ \t]+",text[a:end]) if m.end()>=110]
            if spaces:end=a+spaces[-1]
            out.append((a,end));a=end
        if a<b:out.append((a,b))
    return out

def packet(s,t,c,m):
    span_map.validate(m,s,t)
    sc,tc=c["source_core"],c["translation_core"];sw,tw=c["source_window"],c["translation_window"]
    sr={};tr={};pairs=[]
    def cells(text,row,side,prefix,table):
        result=[]
        for i,(a,b) in enumerate(intervals(text,*row[side]),1):
            key=row["id"]+"."+prefix+str(i);table[key]=(a,b);result.append([key,text[a:b]])
        return result
    for row in m["spans"]:
        a,b=row["source"];x,y=row["target"]
        if sc[0]<=a<b<=sc[1] and tc[0]<=x<y<=tc[1]:
            pairs.append({"span":row["id"],"source":cells(s,row,"source","s",sr),
                          "target":cells(t,row,"target","t",tr)})
    if (not pairs or "".join(s[a:b] for a,b in sr.values())!=s[slice(*sc)]
            or "".join(t[a:b] for a,b in tr.values())!=t[slice(*tc)]):
        raise ValueError("mapped packet incomplete")
    return {"pairs":pairs,"before":{"source":s[sw[0]:sc[0]],"target":t[tw[0]:tc[0]]},
            "after":{"source":s[sc[1]:sw[1]],"target":t[tc[1]:tw[1]]}},sr,tr

def select(text,ids,table):
    if (not isinstance(ids,list) or not 1<=len(ids)<=8 or any(not isinstance(k,str) or k not in table for k in ids)
            or len(set(ids))!=len(ids)):
        raise ValueError("unknown or duplicate passage ID")
    ranges=[table[k] for k in ids]
    if any(b!=c for (a,b),(c,d) in zip(ranges,ranges[1:])):
        raise ValueError("noncontiguous passage selection")
    lo,hi=ranges[0][0],ranges[-1][1]
    while lo<hi and text[lo].isspace():lo+=1
    while hi>lo and text[hi-1].isspace():hi-=1
    quote=text[lo:hi]
    if not quote or len(quote)>300 or PROTECTED.search(quote):raise ValueError("invalid selected anchor")
    hits=compact_review.occurrences(text,quote)
    if lo not in hits:raise ValueError("unrepresentable passage address")
    n=hits.index(lo)
    if n:
        if n>=10000:raise ValueError("too many anchor occurrences")
        return {"quote":quote,"occurrence":n},(lo,hi)
    if len(hits)==1:return {"quote":quote,"occurrence":0},(lo,hi)
    siblings=[v for k,v in table.items() if k.split(".")[0]==ids[0].split(".")[0]]
    left=min([lo]+[a for a,b in siblings]);right=max([hi]+[b for a,b in siblings])
    for n in range(1,301-len(quote)):
        for l in (0,n,n//2):
            a,b=lo-l,hi+n-l
            if a<left or b>right:continue
            q=text[a:b]
            if not PROTECTED.search(q) and text.count(q)==1:return {"quote":q,"occurrence":0},(lo,hi)
    raise ValueError("first repeated address cannot be represented uniquely")

def response_contract(*, followup=False):
    """Wire format only; pinned literary requirements and validators still apply."""
    return (
        "\n\nID-ADDRESSED REVIEW RESPONSE PROTOCOL (runtime adapter): "
        "For this call the model-facing JSON format is the ID format specified below, "
        "not the pinned contract's illustrative canonical quote/occurrence report. "
        "The runtime compiles IDs to exact v2 anchors and attaches review provenance. "
        "Return precisely one JSON object with keys outcome, checks, findings, glossary, style_guide. "
        "Each finding has exactly source_ids, target_ids, category, severity, detail, prior, resolved. "
        "Do not include evidence_version, context, id, source_anchor, translation_anchor or resolves "
        "in the model-facing response. Do not copy quotes. Use actual supplied passage IDs only. "
        "All five checks must be JSON booleans true only after performing them; never fabricate evidence. "
        'For an actual clean first review the complete shape is {"outcome":"PASS","checks":'
        '{"coverage":true,"meaning":true,"terminology":true,"style":true,"structure":true},'
        '"findings":[],"glossary":[],"style_guide":[]}. This is a format example, not an outcome instruction. '
        "For a follow-up include every required prior recheck, including resolved findings. "
        "Do not mark a new finding resolved. Use CORRECTIONS_REQUIRED when any issue remains. "
        "Return JSON only, without commentary."
        + (' For this bounded follow-up, {"outcome":"FULL_REQUIRED"} is the sole alternative '
           "when the supplied evidence is insufficient; it is not approval." if followup else "")
    )


def build_user(data,chunk,total,metadata,glossary,style,prior,s,sr):
    old=[]
    for f in prior:
        a=f["source_anchor"];at=compact_review.occurrences(s,a["quote"])[a["occurrence"]]
        ids=[k for k,(x,y) in sr.items() if x<at+len(a["quote"]) and at<y]
        if not ids:raise ValueError("prior source anchor absent from addressed packet")
        old.append({"id":f["id"],"category":f["category"],"severity":f["severity"],"detail":f["detail"],"source_ids":ids})
    return ("ID-addressed independent review "+chunk["id"]+" of "+str(total)+". "
        "Compare every source-owned span against its actual target, all five literary checks. "
        "Span ownership is provenance, not proof of fidelity. Read context for continuity; find errors only in source core. "
        "Do not report acceptable stylistic alternatives. Return JSON only: "
        '{"outcome":"PASS or CORRECTIONS_REQUIRED","checks":{"coverage":true,"meaning":true,"terminology":true,"style":true,"structure":true},'
        '"findings":[{"source_ids":["s00001.s1"],"target_ids":["s00001.t1"],"category":"meaning","severity":"minor",'
        '"detail":"specific source discrepancy and required fix","prior":null,"resolved":false}],"glossary":[],"style_guide":[]}. '
        "Use real supplied IDs, never copy quotes or generate offsets. Prefer one self-contained sentence/clause; "
        "select contiguous IDs only, combined length <=300 characters, no markers or blank-line boundary. "
        "target_ids:null only for a genuine unresolved omission without a suitable target passage. "
        "Recheck every previous finding with prior set to its ID and the same source passage; newly reported findings "
        "use prior:null,resolved:false. PASS only after all checks and zero unresolved findings. "
        "Categories omission|addition|meaning|modality|referent|terminology|register|voice|style|structure; minor|major|critical. "
        "Use pinned glossary/style schemas, at most eight significant new terms and three observations.\nMetadata: "+
        json.dumps(metadata,ensure_ascii=False)+"\nGlossary:\n"+glossary+"\nStyle:\n"+style+
        "\nPrior findings:\n"+json.dumps(old,ensure_ascii=False,separators=(",",":"))+
        "\nExact source-owned cells:\n"+json.dumps(data,ensure_ascii=False,separators=(",",":")))

def compile_response(answer,s,t,c,m,prior):
    _,sr,tr=packet(s,t,c,m)
    return compile_tables(answer,s,t,sr,tr,prior)

def compile_tables(answer,s,t,sr,tr,prior):
    try:raw=json.loads(answer)
    except (ValueError,TypeError) as exc:raise ValueError("invalid addressed JSON") from exc
    # Fixed diagnostic codes: do not expose the model response or book text.
    if not isinstance(raw,dict) or set(raw)!={"outcome","checks","findings","glossary","style_guide"}:
        raise ValueError("invalid addressed review evidence: envelope fields")
    if raw["outcome"] not in {"PASS","CORRECTIONS_REQUIRED"}:
        raise ValueError("invalid addressed review evidence: outcome")
    if (not isinstance(raw["checks"],dict)
            or raw["checks"]!=dict.fromkeys(compact_review.CHECKS,True)
            or any(type(v) is not bool for v in raw["checks"].values())):
        raise ValueError("invalid addressed review evidence: checks")
    if not isinstance(raw["findings"],list) or len(raw["findings"])>100:
        raise ValueError("invalid addressed review evidence: findings")
    if not isinstance(raw["glossary"],list) or not isinstance(raw["style_guide"],list):
        raise ValueError("invalid addressed review evidence: literary proposal arrays")
    old={f["id"]:f for f in prior};seen=set();items=[]
    for i,f in enumerate(raw["findings"],1):
        if (not isinstance(f,dict) or set(f)!={"source_ids","target_ids","category","severity","detail","prior","resolved"}
                or type(f["resolved"]) is not bool):raise ValueError("invalid addressed finding")
        sa,(a,b)=select(s,f["source_ids"],sr)
        if f["target_ids"] is None:
            if f["category"]!="omission" or f["resolved"]:raise ValueError("missing target requires unresolved omission")
            ta=None
        else:ta,_=select(t,f["target_ids"],tr)
        fid=f["prior"];res=None
        if fid is None:
            if f["resolved"]:raise ValueError("new finding cannot resolve itself")
        else:
            if not isinstance(fid,str) or fid not in old or fid in seen:raise ValueError("invalid prior lineage")
            q=old[fid]["source_anchor"];at=compact_review.occurrences(s,q["quote"])[q["occurrence"]]
            if not (a<at+len(q["quote"]) and at<b):raise ValueError("recheck selects another source passage")
            seen.add(fid);res={"record_id":old[fid]["previous_record_id"],"finding_id":fid}
        items.append({"id":"f%03d"%i,"category":f["category"],"severity":f["severity"],"detail":f["detail"],
                      "resolved":f["resolved"],"source_anchor":sa,"translation_anchor":ta,"resolves":res})
    if seen!=set(old) or ((raw["outcome"]=="PASS")==any(not f["resolved"] for f in items)):
        raise ValueError("incomplete rechecks or inconsistent outcome")
    return json.dumps({**raw,"evidence_version":2,"findings":items},ensure_ascii=False)
