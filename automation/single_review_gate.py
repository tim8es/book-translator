"""Consolidate routine edit review into the canonical independent recheck.

Machine acceptance is not literary PASS. High-risk edits still get a separate
independent preflight. Every accepted correction still requires independent
review of the exact changed artifact before promotion.
"""
import os
import re

def enabled():
    opt=os.environ.get("BOOK_TRANSLATOR_SINGLE_REVIEW_GATE","off")
    if opt not in {"on","off"}:
        raise ValueError("invalid single review gate flag")
    return opt=="on"

def paragraph_count(text):
    return len(re.split(r"\n[ \t]*\n",text.strip()))

def risky(p,f):
    old=p["target_anchor"]["quote"]
    new=p["replacement"]
    return (p["operation"]!="replace" or not new.strip()
            or f["severity"]=="critical"
            or f["category"] in {"omission","addition","structure"}
            or (f["severity"]=="major" and f["category"] in {"meaning","modality","referent","voice","register"})
            or paragraph_count(old)!=paragraph_count(new)
            or (len(old)>=30 and len(new)*100<len(old)*80)
            or len(new)>max(350,3*len(old)))

def select_cases(cases,patches,findings):
    if not enabled():
        return cases
    by_id={f["id"]:f for f in findings}
    risky_ids={p["finding_id"] for p in patches if risky(p,by_id[p["finding_id"]])}
    return [case for case in cases if risky_ids.intersection(case["patch_ids"])]

def paragraph_preserving():
    opt=os.environ.get("BOOK_TRANSLATOR_PARAGRAPH_REVIEW","off")
    if opt not in {"on","off"}:
        raise ValueError("invalid paragraph review flag")
    return opt=="on"
