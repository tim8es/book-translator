"""Exact source-owned translation coordinates, not literary approval."""
import hashlib
import os

def enabled():
    mode=os.environ.get("BOOK_TRANSLATOR_SPAN_MAP","off")
    if mode not in {"on","off"}:raise ValueError("invalid span map mode")
    return mode=="on"

def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()

def entry(i,sc,tc,s,t):
    return {"id":"s%05d"%i,"source":list(sc),"target":list(tc),
            "source_sha256":sha(s[slice(*sc)]),"target_sha256":sha(t[slice(*tc)])}

def validate(m,s,t):
    if (not isinstance(m,dict) or set(m)!={"schema_version","source_sha256","translation_sha256","spans"}
            or type(m["schema_version"]) is not int or m["schema_version"]!=1
            or m["source_sha256"]!=sha(s) or m["translation_sha256"]!=sha(t)
            or not isinstance(m["spans"],list) or not 1<=len(m["spans"])<=20000):
        raise ValueError("invalid or stale span map")
    x=y=0
    for i,row in enumerate(m["spans"],1):
        if not isinstance(row,dict) or set(row)!={"id","source","target","source_sha256","target_sha256"}:
            raise ValueError("invalid span fields")
        sc,tc=row["source"],row["target"]
        if (not isinstance(sc,list) or not isinstance(tc,list) or len(sc)!=2 or len(tc)!=2
                or any(type(v) is not int for v in sc+tc) or sc[0]!=x or tc[0]!=y
                or not x<sc[1]<=len(s) or not y<tc[1]<=len(t)
                or row!=entry(i,sc,tc,s,t)):
            raise ValueError("span coordinate or digest mismatch")
        x,y=sc[1],tc[1]
    if x!=len(s) or y!=len(t):raise ValueError("map leaves unowned text")

def make(s,t,pairs):
    m={"schema_version":1,"source_sha256":sha(s),"translation_sha256":sha(t),
       "spans":[entry(i,sc,tc,s,t) for i,(sc,tc) in enumerate(pairs,1)]}
    validate(m,s,t)
    return m

def from_render(plan,segments,s,t):
    """Derive coordinates from actual helper inputs and verify exact output."""
    if plan.get("source_sha256")!=sha(s):raise ValueError("source mismatch")
    orig=[];target=[];cuts=[(0,0)];x=y=0;seen=[]
    for p in plan["parts"]:
        if p["kind"]=="text":
            key=p["id"]
            if key!="s%05d"%(len(seen)+1) or key not in segments:
                raise ValueError("missing source-owned segment")
            if seen:cuts.append((x,y))
            seen.append(key)
            u=p["before"]+p["original"]+p["after"]
            v=p["before"]+segments[key].strip()+p["after"]
        else:u=v=p["value"]
        orig.append(u);target.append(v);x+=len(u);y+=len(v)
    if not seen or set(seen)!=set(segments) or "".join(orig)!=s or "".join(target).rstrip()+"\n"!=t:
        raise ValueError("map cannot reproduce rendered artifacts")
    cuts.append((len(s),len(t)))
    return make(s,t,[([a,c],[b,d]) for (a,b),(c,d) in zip(cuts,cuts[1:])])

def after_patches(m,s,before,after,patches,position):
    """Return None for ambiguous ownership; never guess changed alignment."""
    validate(m,s,before)
    edits=[];rows=m["spans"]
    for p in patches:
        a,b=position(before,p["target_anchor"])
        owners=[i for i,row in enumerate(rows) if row["target"][0]<=a<b<=row["target"][1]]
        if len(owners)!=1:return None
        if p["operation"]=="insert_before":b=a
        elif p["operation"]=="insert_after":a=b
        elif p["operation"]!="replace":raise ValueError("unknown edit")
        edits.append((a,b,p["replacement"],owners[0]))
    edits.sort(key=lambda e:(e[0],e[1]));cursor=0;parts=[];delta=[0]*len(rows)
    for a,b,r,i in edits:
        if a<cursor:raise ValueError("overlapping edits")
        parts.extend((before[cursor:a],r));cursor=b;delta[i]+=len(r)-(b-a)
    parts.append(before[cursor:])
    if "".join(parts)!=after:raise ValueError("map cannot reproduce corrected artifact")
    pairs=[];offset=0
    for i,row in enumerate(rows):
        a,b=row["target"];end=offset+b-a+delta[i]
        if end<=offset:return None
        pairs.append((row["source"],[offset,end]));offset=end
    return make(s,after,pairs)

def groups(m,s,t,budget,window):
    validate(m,s,t)
    if type(budget) is not int or not 3000<=budget<=12000:raise ValueError("invalid map budget")
    rows=m["spans"];start=0;out=[]
    while start<len(rows):
        best=None
        for end in range(start+1,len(rows)+1):
            sc=[rows[start]["source"][0],rows[end-1]["source"][1]]
            tc=[rows[start]["target"][0],rows[end-1]["target"][1]]
            sw,tw=window(s,*sc),window(t,*tc)
            if sw[1]-sw[0]+tw[1]-tw[0]>42000:break
            if best and sc[1]-sc[0]>budget:break
            best=(end,sc,tc,sw,tw)
        if best is None:return None
        end,sc,tc,sw,tw=best
        out.append({"id":"chunk-%03d"%(len(out)+1),"source_core":sc,"translation_core":tc,"source_window":sw,"translation_window":tw})
        start=end
    return out
