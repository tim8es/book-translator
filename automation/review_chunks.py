"""Source/translation-bound resumable review partitioning for oversized units.

Pure, deterministic public-runtime helper; canonical reviewer evidence and its
acceptance remain owned by the pinned literary workflow. Source is never edited.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import resilient_recovery

SCHEMA_VERSION = 1
HELPER_REVISION = "review-chunks-v1"
ALIGNMENT_REVISION = "review-chunks-markers-v1"
STABLE_ALIGNMENT_REVISION = "review-chunks-source-stable-v1"
PARAGRAPH_STABLE_REVISION = "review-chunks-paragraph-stable-v1"
ALIGNED_REVISIONS = {ALIGNMENT_REVISION, STABLE_ALIGNMENT_REVISION, PARAGRAPH_STABLE_REVISION}
REFINED_REVISION="review-chunks-refined-v1"
PLAN_REVISIONS={HELPER_REVISION,REFINED_REVISION}|ALIGNED_REVISIONS
BASE_FIELDS={"schema_version","helper_revision","source_sha256","translation_sha256","chunks"}

def plan_fields(plan):
    return BASE_FIELDS|({"base_plan","split_steps"} if plan.get("helper_revision")==REFINED_REVISION else set())
BLOCK_MARKER = re.compile(r"<!--\s*bt-block:(\d+)\s*-->")
TARGET_CHARS = 12_000
OVERLAP_CHARS = 1_800
MAX_CHUNKS = 64


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cuts(text: str, count: int) -> list[int]:
    """Find strictly increasing paragraph-aligned boundaries."""
    candidates = [m.end() for m in re.finditer(r"\n[ \t]*\n", text)]
    if len(candidates) < count - 1:
        raise ValueError("too few paragraph boundaries for bounded review")
    result = [0]
    for part in range(1, count):
        target = len(text) * part / count
        remaining = count - part
        available = [pos for pos in candidates
                     if pos > result[-1] + 500
                     and len(text) - pos >= 500 * remaining
                     and sum(1 for candidate in candidates if candidate > pos) >= remaining - 1]
        if not available:
            raise ValueError("cannot safely partition reviewer paragraphs")
        result.append(min(available, key=lambda pos: abs(pos - target)))
    result.append(len(text))
    if any(right <= left for left, right in zip(result, result[1:])):
        raise ValueError("review partitions do not advance")
    return result


def _window(text: str, start: int, end: int) -> list[int]:
    """Expand to nearby full paragraphs, never cutting inside a paragraph."""
    left = max(0, start - OVERLAP_CHARS)
    right = min(len(text), end + OVERLAP_CHARS)
    if left > 0:
        found = text.rfind("\n\n", 0, left)
        left = found + 2 if found >= 0 else 0
    if right < len(text):
        found = text.find("\n\n", right)
        right = found + 2 if found >= 0 else len(text)
    return [left, right]


def plan(source: str, translation: str, *, target_chars: int = TARGET_CHARS) -> dict:
    if not source.strip() or not translation.strip():
        raise ValueError("review requires both complete source and translation")
    if type(target_chars) is not int or not 3000 <= target_chars <= TARGET_CHARS:
        raise ValueError("invalid adaptive review target")
    count = max(2, math.ceil(max(len(source), len(translation)) / target_chars))
    if count > MAX_CHUNKS:
        raise ValueError("source exceeds bounded review capacity")
    sb = _cuts(source, count)
    tb = _cuts(translation, count)
    chunks = []
    for i in range(count):
        sc, tc = [sb[i], sb[i + 1]], [tb[i], tb[i + 1]]
        sw, tw = _window(source, *sc), _window(translation, *tc)
        if (sw[0] > sc[0] or sw[1] < sc[1]
                or tw[0] > tc[0] or tw[1] < tc[1]
                or sw[1] - sw[0] + tw[1] - tw[0] > 42_000):
            raise ValueError("review chunk cannot fit the safe context budget")
        chunks.append({"id": "chunk-%03d" % (i + 1), "source_core": sc,
                       "translation_core": tc, "source_window": sw,
                       "translation_window": tw})
    result = {"schema_version": SCHEMA_VERSION, "helper_revision": HELPER_REVISION,
              "source_sha256": sha(source), "translation_sha256": sha(translation),
              "chunks": chunks}
    validate_plan(result, source, translation)
    return result



def _marker_cuts(source: str, translation: str, *, count: int | None = None,
                 target_chars: int = TARGET_CHARS,
                 stable: bool = False) -> tuple[list[int], list[int]] | None:
    """Find paired boundaries at exact protected EPUB block IDs.

    Unlike independent target-length cuts, corresponding cores start/end on
    the same source-owned block markers, even when target language expands.
    No marker is ever inferred, invented, or removed. A mismatched sequence
    fails closed rather than certifying potentially misaligned passages.
    """
    sm = list(BLOCK_MARKER.finditer(source))
    tm = list(BLOCK_MARKER.finditer(translation))
    if not sm and not tm:
        return None
    ids = [item.group(1) for item in sm]
    if (not sm or not tm or ids != [item.group(1) for item in tm]
            or len(ids) != len(set(ids))):
        raise ValueError("source and translation EPUB block markers are not aligned")
    if len(sm) < 2:
        return None
    src = [0] + [item.start() for item in sm[1:]] + [len(source)]
    dst = [0] + [item.start() for item in tm[1:]] + [len(translation)]
    masses = [(src[i + 1] - src[i]) if stable else
              max(src[i + 1] - src[i], dst[i + 1] - dst[i])
              for i in range(len(sm))]
    if type(target_chars) is not int or not 3000 <= target_chars <= TARGET_CHARS:
        raise ValueError("invalid adaptive review target")
    desired = (count if count is not None else
               min(len(sm), max(2, math.ceil(max(len(source), len(translation))
                                             / target_chars))))
    if not 2 <= desired <= len(sm):
        raise ValueError("invalid aligned review partition count")
    if desired > MAX_CHUNKS:
        raise ValueError("source exceeds bounded aligned review capacity")
    cumul = [0]
    for mass in masses:
        cumul.append(cumul[-1] + mass)
    indices = [0]
    for part in range(1, desired):
        remaining = desired - part
        options = range(indices[-1] + 1, len(sm) - remaining + 1)
        if not options:
            raise ValueError("cannot safely align review block partitions")
        target = cumul[-1] * part / desired
        indices.append(min(options, key=lambda i: (abs(cumul[i] - target), i)))
    indices.append(len(sm))
    scuts, tcuts = [src[index] for index in indices], [dst[index] for index in indices]
    if any(right <= left for left, right in zip(scuts, scuts[1:])) or any(
            right <= left for left, right in zip(tcuts, tcuts[1:])):
        raise ValueError("aligned review block partitions do not advance")
    return scuts, tcuts


def plan_aligned(source: str, translation: str, *,
                 target_chars: int = TARGET_CHARS, stable: bool = False,
                 count: int | None = None) -> dict:
    """Opt-in marker-aligned reviewer plan; legacy fallback when no markers.

    This is a new helper revision for *new* checkpoints. Legacy checkpoints
    remain on review-chunks-v1; no historical evidence is silently converted.
    """
    if not source.strip() or not translation.strip():
        raise ValueError("review requires both complete source and translation")
    pair = _marker_cuts(source, translation, target_chars=target_chars,
                        stable=stable, count=count)
    if pair is None:
        return plan(source, translation, target_chars=target_chars)
    sb, tb = pair
    chunks = []
    for i in range(len(sb) - 1):
        sc, tc = [sb[i], sb[i + 1]], [tb[i], tb[i + 1]]
        sw, tw = _window(source, *sc), _window(translation, *tc)
        if (sw[0] > sc[0] or sw[1] < sc[1]
                or tw[0] > tc[0] or tw[1] < tc[1]
                or sw[1] - sw[0] + tw[1] - tw[0] > 42_000):
            raise ValueError("aligned review chunk exceeds safe context budget")
        chunks.append({"id": "chunk-%03d" % (i + 1), "source_core": sc,
                       "translation_core": tc, "source_window": sw,
                       "translation_window": tw})
    result = {"schema_version": SCHEMA_VERSION,
              "helper_revision": STABLE_ALIGNMENT_REVISION if stable else ALIGNMENT_REVISION,
              "source_sha256": sha(source), "translation_sha256": sha(translation),
              "chunks": chunks}
    validate_plan(result, source, translation)
    return result


def _paragraph_cuts(source,translation,count):
    """Ordinal correspondence is a planning hypothesis, not semantic evidence."""
    if BLOCK_MARKER.search(source) or BLOCK_MARKER.search(translation):
        return None
    src=[0]+[m.end() for m in re.finditer(r"\n[ \t]*\n",source) if m.end()<len(source)]+[len(source)]
    dst=[0]+[m.end() for m in re.finditer(r"\n[ \t]*\n",translation) if m.end()<len(translation)]+[len(translation)]
    if len(src)!=len(dst) or len(src)<count+1 or not 2<=count<=MAX_CHUNKS:
        return None
    indices=[0]
    for i in range(1,count):
        choices=range(indices[-1]+1,len(src)-(count-i))
        if not choices:
            return None
        indices.append(min(choices,key=lambda j:(abs(src[j]-len(source)*i/count),j)))
    indices.append(len(src)-1)
    return [src[j] for j in indices],[dst[j] for j in indices]


def plan_paragraphs(source,translation,*,target_chars=TARGET_CHARS,count=None,wider=False):
    if not source.strip() or not translation.strip():
        return None
    if type(target_chars) is not int or not 3000<=target_chars<=TARGET_CHARS:
        raise ValueError("invalid adaptive paragraph target")
    # Try 16k-character cores, but never exceed the existing combined window
    # ceiling. On provider pressure use the ordinary smaller target.
    desired=count if count is not None else max(2,math.ceil(max(len(source),len(translation))/(16000 if wider else target_chars)))
    for n in ([desired] if count is not None else range(desired,min(MAX_CHUNKS,desired+3)+1)):
        pair=_paragraph_cuts(source,translation,n)
        if pair is None:
            return None
        sb,tb=pair
        pieces=[]
        for i in range(n):
            sc,tc=[sb[i],sb[i+1]],[tb[i],tb[i+1]]
            sw,tw=_window(source,*sc),_window(translation,*tc)
            if sw[1]-sw[0]+tw[1]-tw[0]>42000:
                break
            pieces.append({"id":"chunk-%03d"%(i+1),"source_core":sc,"translation_core":tc,
                           "source_window":sw,"translation_window":tw})
        if len(pieces)==n:
            result={"schema_version":SCHEMA_VERSION,"helper_revision":PARAGRAPH_STABLE_REVISION,
                    "source_sha256":sha(source),"translation_sha256":sha(translation),"chunks":pieces}
            validate_plan(result,source,translation)
            return result
    return None


def _split_part(part,source,translation,base_revision):
    sc,tc=part["source_core"],part["translation_core"]
    def interior(text,interval,markers=False):
        lo,hi=interval
        if markers:
            return [(m.group(1),m.start()) for m in BLOCK_MARKER.finditer(text,lo,hi) if lo<m.start()<hi]
        return [(i,m.end()) for i,m in enumerate(re.finditer(r"\n[ \t]*\n",text[lo:hi])) if 0<m.end()<hi-lo]
    marked=base_revision in {ALIGNMENT_REVISION,STABLE_ALIGNMENT_REVISION}
    if marked:
        sa,ta=interior(source,sc,True),interior(translation,tc,True)
        if [k for k,_ in sa]!=[k for k,_ in ta] or not sa:
            return None
        i=min(range(len(sa)),key=lambda i:abs(sa[i][1]-sum(sc)/2))
        s,t=sa[i][1],ta[i][1]
    else:
        sa=[sc[0]+x for _,x in interior(source,sc)]
        ta=[tc[0]+x for _,x in interior(translation,tc)]
        # interior() enumerates local paragraph separators. Stable ordinal
        # boundaries require equal counts; legacy planning stays approximate.
        if not sa or not ta:
            return None
        if base_revision==PARAGRAPH_STABLE_REVISION:
            if len(sa)!=len(ta):
                return None
            i=min(range(len(sa)),key=lambda i:abs(sa[i]-sum(sc)/2))
            s,t=sa[i],ta[i]
        else:
            s=min(sa,key=lambda x:abs(x-sum(sc)/2))
            t=min(ta,key=lambda x:abs(x-sum(tc)/2))
    if min(s-sc[0],sc[1]-s,t-tc[0],tc[1]-t)<500:
        return None
    result=[]
    for a,b,c,d in ((sc[0],s,tc[0],t),(s,sc[1],t,tc[1])):
        sw,tw=_window(source,a,b),_window(translation,c,d)
        if sw[1]-sw[0]+tw[1]-tw[0]>42000:
            return None
        result.append({"source_core":[a,b],"translation_core":[c,d],"source_window":sw,"translation_window":tw})
    return result

def _refined_chunks(base,steps,source,translation):
    parts=[dict(p) for p in base["chunks"]]
    if not isinstance(steps,list) or not 1<=len(steps)<=MAX_CHUNKS-len(parts):
        raise ValueError("invalid review refinement count")
    for i in steps:
        if type(i) is not int or not 0<=i<len(parts):
            raise ValueError("invalid review refinement index")
        pair=_split_part(parts[i],source,translation,base["helper_revision"])
        if pair is None:
            raise ValueError("review refinement cannot safely split part")
        parts[i:i+1]=pair
    return [{**part,"id":"chunk-%03d"%(i+1)} for i,part in enumerate(parts)]

def refine_pending(plan,part_id,source,translation):
    validate_plan(plan,source,translation)
    i=next((i for i,p in enumerate(plan["chunks"]) if p["id"]==part_id),None)
    if i is None or len(plan["chunks"])>=MAX_CHUNKS:
        return None
    base=plan["base_plan"] if plan["helper_revision"]==REFINED_REVISION else plan
    steps=plan["split_steps"]+[i] if plan["helper_revision"]==REFINED_REVISION else [i]
    try:
        parts=_refined_chunks(base,steps,source,translation)
    except ValueError:
        return None
    result={"schema_version":SCHEMA_VERSION,"helper_revision":REFINED_REVISION,
            "source_sha256":sha(source),"translation_sha256":sha(translation),"chunks":parts,
            "base_plan":base,"split_steps":steps}
    validate_plan(result,source,translation)
    return result


def validate_plan(value: dict, source: str, translation: str) -> None:
    if (not isinstance(value, dict) or set(value) != plan_fields(value)
            or value["schema_version"] != SCHEMA_VERSION
            or value["helper_revision"] not in PLAN_REVISIONS
            or value["source_sha256"] != sha(source)
            or value["translation_sha256"] != sha(translation)
            or not isinstance(value["chunks"], list)
            or not 2 <= len(value["chunks"]) <= MAX_CHUNKS):
        raise ValueError("review plan is missing or stale")
    for kind, text in (("source", source), ("translation", translation)):
        offset = 0
        for i, item in enumerate(value["chunks"]):
            if not isinstance(item, dict) or set(item) != {
                    "id", "source_core", "translation_core",
                    "source_window", "translation_window"}:
                raise ValueError("invalid review chunk")
            if item["id"] != "chunk-%03d" % (i + 1):
                raise ValueError("out-of-order review chunk")
            core, window = item[kind + "_core"], item[kind + "_window"]
            if (not isinstance(core, list) or not isinstance(window, list)
                    or len(core) != 2 or len(window) != 2
                    or any(type(v) is not int for v in core + window)
                    or core[0] != offset or not offset < core[1] <= len(text)
                    or not 0 <= window[0] <= core[0]
                    or not core[1] <= window[1] <= len(text)):
                raise ValueError("review chunks do not cover the complete artifact")
            offset = core[1]
        if offset != len(text):
            raise ValueError("review final chunk leaves unreviewed text")
    # Persisted plans must be deterministic, not user/model supplied.
    if value["helper_revision"]==REFINED_REVISION:
        base=value["base_plan"]
        if not isinstance(base,dict) or base.get("helper_revision") not in PLAN_REVISIONS-{REFINED_REVISION}:
            raise ValueError("invalid refined review base plan")
        validate_plan(base,source,translation)
        if value["chunks"]!=_refined_chunks(base,value["split_steps"],source,translation):
            raise ValueError("refined review partition was modified")
        return
    if value["helper_revision"]==PARAGRAPH_STABLE_REVISION:
        aligned=_paragraph_cuts(source,translation,len(value["chunks"]))
        if aligned is None:
            raise ValueError("paragraph review plan has lost boundaries")
        source_cuts,target_cuts=aligned
    elif value["helper_revision"] in ALIGNED_REVISIONS:
        aligned = _marker_cuts(source, translation, count=len(value["chunks"]),
                               stable=value["helper_revision"] == STABLE_ALIGNMENT_REVISION)
        if aligned is None or len(aligned[0]) != len(value["chunks"]) + 1:
            raise ValueError("aligned review plan has lost marker boundaries")
        source_cuts, target_cuts = aligned
    else:
        source_cuts = _cuts(source, len(value["chunks"]))
        target_cuts = _cuts(translation, len(value["chunks"]))
    for kind, text, cuts in (
            ("source", source, source_cuts),
            ("translation", translation, target_cuts)):
        for i, item in enumerate(value["chunks"]):
            if (item[kind + "_core"] != [cuts[i], cuts[i + 1]]
                    or item[kind + "_window"] != _window(text, cuts[i], cuts[i + 1])):
                raise ValueError("review partition was modified")


def validate_stage(stage: dict, plan_value: dict, unit_id: str, revision: str) -> None:
    if (not isinstance(stage, dict)
            or set(stage) != {"schema_version", "plan", "unit_id", "workflow_revision", "completed"}
            or stage["schema_version"] != SCHEMA_VERSION
            or stage["plan"] != plan_value or stage["unit_id"] != unit_id
            or stage["workflow_revision"] != revision
            or not isinstance(stage["completed"], dict)):
        raise ValueError("review checkpoint is stale or malformed")
    ids = {part["id"] for part in plan_value["chunks"]}
    if any(key not in ids or not isinstance(value, dict)
           or set(value) not in ({"outcome", "report", "style"},
                                 {"outcome", "report", "style", "glossary"})
           or ("glossary" in value and not isinstance(value["glossary"], list))
           for key, value in stage["completed"].items()):
        raise ValueError("review checkpoint has unexpected evidence")


def new_stage(plan_value: dict, unit_id: str, revision: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "plan": plan_value,
            "unit_id": unit_id, "workflow_revision": revision, "completed": {}}


def _evidence_offset(text: str, anchor: dict) -> int:
    quote=anchor["quote"]
    occurrence=anchor["occurrence"]
    if not isinstance(quote,str) or not quote or type(occurrence) is not int or occurrence<0:
        raise ValueError("invalid review evidence anchor")
    hits=[];cursor=0
    while True:
        at=text.find(quote,cursor)
        if at<0:
            break
        hits.append(at);cursor=at+len(quote)
    if occurrence>=len(hits) or (occurrence==0 and len(hits)!=1):
        raise ValueError("review evidence quote is absent or ambiguous")
    return hits[occurrence]


def find_previous_for_chunk(previous: dict | None, chunk: dict, source: str) -> list[dict]:
    if not previous:
        return []
    report = previous.get("quality_report", {})
    if not isinstance(report, dict) or report.get("evidence_version") != 2:
        return []
    result = []
    for item in report["findings"]:
        if item["resolved"]:
            continue
        anchor = item["source_anchor"]
        offset = _evidence_offset(source, anchor)
        if chunk["source_core"][0] <= offset < chunk["source_core"][1]:
            result.append({
                "id": item["id"], "category": item["category"],
                "severity": item["severity"], "detail": item["detail"],
                "source_anchor": anchor,
                "previous_translation_anchor": item["translation_anchor"],
                "previous_record_id": previous["record_id"],
            })
    return result


def validate_chunk_report(chunk: dict, report: dict, source: str,
                          translation: str) -> None:
    if (not isinstance(report, dict) or report.get("evidence_version") != 2
            or report.get("checks") != dict.fromkeys(
                ("coverage", "meaning", "terminology", "style", "structure"), True)
            or not isinstance(report.get("findings"), list)):
        raise ValueError("chunk lacks complete reviewer checks")
    for item in report["findings"]:
        src = item["source_anchor"]
        spos = _evidence_offset(source, src)
        if (spos < chunk["source_core"][0]
                or spos >= chunk["source_core"][1]):
            raise ValueError("review finding not anchored to the assigned source core")
        tgt = item["translation_anchor"]
        if tgt is not None:
            tpos = _evidence_offset(translation, tgt)
            if (tpos < chunk["translation_window"][0]
                    or tpos >= chunk["translation_window"][1]):
                raise ValueError("review finding not anchored to the corresponding translation")
        if item["id"].startswith("chunk-"):
            raise ValueError("review chunk IDs must be local and unprefixed")


def combine(stage: dict, *, status=None) -> dict:
    """Return raw JSON schema for one canonical, still-unaccepted review."""
    chunks = stage["plan"]["chunks"]
    if set(stage["completed"]) != {p["id"] for p in chunks}:
        raise ValueError("full reviewer coverage has not been established")
    all_findings, all_style, all_glossary = [], [], []
    seen_prior = set()
    for part in chunks:
        stored = stage["completed"][part["id"]]
        if stored["outcome"] not in {"PASS", "CORRECTIONS_REQUIRED"}:
            raise ValueError("invalid completed review outcome")
        report = stored["report"]
        if report.get("checks") != dict.fromkeys(
                ("coverage", "meaning", "terminology", "style", "structure"), True):
            raise ValueError("review checks missing from chunk")
        if stored["outcome"] == "PASS" and any(
                not x["resolved"] for x in report["findings"]):
            raise ValueError("chunk claims PASS despite unresolved findings")
        for finding in report["findings"]:
            item = dict(finding)
            item["id"] = part["id"] + "-" + item["id"]
            resolves = item.get("resolves")
            if resolves is not None:
                key = (resolves["record_id"], resolves["finding_id"])
                if key in seen_prior:
                    raise ValueError("prior finding was reviewed more than once")
                seen_prior.add(key)
            all_findings.append(item)
        for item in stored["style"]:
            if item not in all_style:
                all_style.append(item)
        for item in stored.get("glossary", []):
            if item not in all_glossary:
                all_glossary.append(item)
    if resilient_recovery.enabled():
        all_glossary,g=resilient_recovery.bounded_memory([],all_glossary)
        all_style,s=resilient_recovery.bounded_memory([],all_style)
        if status is not None and g+s:
            status["optional_memory_proposals_deferred"]=status.get("optional_memory_proposals_deferred",0)+g+s
    elif len(all_glossary) > 40:
        raise ValueError("aggregated glossary exceeds bounded reviewer memory")
    if len(all_findings) > 100:
        raise ValueError("aggregated review exceeds canonical findings limit")
    unresolved = any(not f["resolved"] for f in all_findings)
    outcome = ("CORRECTIONS_REQUIRED" if unresolved else "PASS")
    if any(stage["completed"][part["id"]]["outcome"] == "CORRECTIONS_REQUIRED"
           for part in chunks) and not unresolved:
        raise ValueError("chunk requests corrections without unresolved evidence")
    result = {"evidence_version": 2, "outcome": outcome,
              "checks": dict.fromkeys(
                  ("coverage", "meaning", "terminology", "style", "structure"), True),
              "findings": all_findings, "style_guide": all_style}
    if all_glossary:
        result["glossary"] = all_glossary
    return result
