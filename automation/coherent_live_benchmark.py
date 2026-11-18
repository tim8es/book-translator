#!/usr/bin/env python3
"""Live A/B using real Auto and isolated local Git histories, no book-state writes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
PIN = "a973f29e9bfba255056b5196a5a2943578d1cc29"
JOB = "b" * 32

PARAGRAPHS = [
    "The clock above the station door had stopped at seven, though the evening train was not due before nine. Mira counted the empty benches and wondered whether the watchmaker had forgotten his promise.",
    "A brass key lay on the windowsill. Anton had left it there that morning, but he had not said which lock it opened. She wrapped the key in a blue handkerchief rather than put it in her pocket.",
    "The porter swept the platform in slow circles. When Mira asked about the missing timetable, he pointed toward the ticket office without looking up. She could hear the sea beyond the railway wall.",
    "At the ticket office, a letter waited under a glass paperweight. The envelope was addressed to Anton, not to her. Mira decided that she would carry it to him unopened.",
    "The last lamp flickered. A woman in a green coat stepped out of the waiting room and asked if the road to the lighthouse was flooded. Mira said she had seen water over the lower steps, but the upper road might still be passable.",
    "They walked together as far as the iron gate. The woman said her name was Elena. She had come to return a borrowed map and would have to take the morning train if she missed the last one.",
    "Mira remembered the map in Anton's workshop: the northern shore was drawn in red ink, the southern shore in pencil. She had always thought that the different colors marked safe routes, but Anton had never confirmed it.",
    "A bell sounded twice behind them. Elena stopped, believing it was the train, until Mira explained that the bell belonged to the lighthouse. The station clock remained silent.",
    "At the workshop, three boxes lay beside the door. The smallest was empty; the middle box held a broken spring; the largest was locked. Mira tried the brass key in the largest box, and it turned without resistance.",
    "Inside was a ledger of repairs, several receipts and the missing timetable. Anton had written a note on the back of the timetable: the train had been delayed, not cancelled. Mira smiled at the distinction.",
    "Elena returned the map to its place. She asked whether Anton would mind if they waited in the workshop. Mira knew he disliked strangers handling his tools, but she was fairly sure he would not object to two chairs by the stove.",
    "The rain eased. Mira placed the unopened letter on the table, beside the key and the repaired clock. For the first time that evening, the hands began to move.",
    "Anton arrived carrying a small parcel. He apologized for the delay and thanked Elena for bringing the map. Then he read the letter aloud: the station would reopen its northern platform the following week.",
    "Mira had expected him to be angry about the ledger. Instead, he said that the locksmith had given her the key precisely so she could find the timetable if he was late. It had not been meant as a secret.",
    "The three of them returned to the railway wall. Elena watched the lights on the water while Anton checked his watch against the station clock. The two clocks disagreed by exactly four minutes.",
    "When the train finally arrived, Elena boarded with the parcel and her ticket. Mira stayed behind to help Anton close the workshop. The gate was still wet, but the road above it had dried.",
    "Before leaving, Mira wrote a note for the porter, explaining where the timetable had been found. She did not mention the letter. It was Anton's news to share, and he had promised to tell the porter himself.",
    "The following morning, the station clock showed eight. Mira took the upper road to the lighthouse and saw the map hanging beside the kitchen door. She was glad that nobody had mistaken the red lines for warnings."
]

def make_epub(path):
    # Fixed ZIP metadata makes both arms byte-identical. All prose is invented.
    extras = [
        "She took care not to confuse what she had observed with what she had only been told.",
        "For a moment she considered asking another question, then decided to wait for an answer.",
        "The details mattered to her because a small mistake could send someone down the wrong road.",
    ]
    body = "".join("<p>" + escape(p + " " + extras[i % len(extras)]) + "</p>" for i,p in enumerate(PARAGRAPHS))
    xhtml = '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>The Station Clock</title></head><body><h1>The Station Clock</h1>' + body + "</body></html>"
    entries = {
        "mimetype":"application/epub+zip",
        "META-INF/container.xml":'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OEBPS/content.opf":'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:synthetic:station-clock</dc:identifier><dc:title>The Station Clock</dc:title><dc:creator>Benchmark Fixture</dc:creator><dc:language>en</dc:language><meta property="dcterms:modified">2026-11-18T00:00:00Z</meta></metadata><manifest><item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="c"/></spine></package>',
        "OEBPS/nav.xhtml":'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">The Station Clock</a></li></ol></nav></body></html>',
        "OEBPS/chapter.xhtml":xhtml,
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name, (2026,11,18,0,0,0))
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            z.writestr(info, data.encode("utf-8"))

def git(path, *args):
    subprocess.run(["git","-C",str(path),*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def run_arm(home, source, mode, cap):
    root = home / mode
    root.mkdir()
    remote = home / (mode + "-bare")
    remote.mkdir()
    git(remote,"init","--bare","--initial-branch=main")
    git(root,"init","--initial-branch=main")
    git(root,"config","user.name","Synthetic benchmark")
    git(root,"config","user.email","benchmark@example.invalid")
    intake = root / "intake" / "auto" / JOB
    intake.mkdir(parents=True)
    (intake/"source.epub").write_bytes(source)
    (intake/"request.json").write_text(json.dumps({"schema_version":1,"job_id":JOB,
        "source_sha256":hashlib.sha256(source).hexdigest(),"target_language":"ru",
        "workflow_revision":PIN}),encoding="utf-8")
    git(root,"add",".")
    git(root,"commit","-m","Synthetic benchmark intake")
    git(root,"remote","add","origin",str(remote))
    git(root,"push","-u","origin","main")
    env = {**os.environ,"BOOK_TRANSLATOR_STATE_ROOT":str(root),
           "BOOK_TRANSLATOR_COHERENT_BATCHES":mode,"OMNIROUTE_MODEL_CALL_LIMIT":str(cap),
           "BOOK_TRANSLATOR_CONTINUE_CORRECTIONS":"on","BOOK_TRANSLATOR_MARKER_ALIGNMENT":"on",
           "BOOK_TRANSLATOR_INCREMENTAL_REVIEW":"on","BOOK_TRANSLATOR_ADAPTIVE_BUDGET":"on",
           "BOOK_TRANSLATOR_QUEUE_BUDGET":"on"}
    for key in ("GITHUB_OUTPUT","BOOK_TRANSLATOR_PINNED_ENGINE","BOOK_TRANSLATOR_PINNED_REVISION"):
        env.pop(key,None)
    status_path = root/"automation"/"status"/(JOB+".json")
    started = time.monotonic()
    status = {}
    reason = "checkpoint-limit"
    for _ in range(120):
        result = subprocess.run([sys.executable,str(ROOT/"automation/book_runner.py"),"--job-id",JOB],
                                cwd=root,env=env,capture_output=True,text=True)
        if status_path.exists():
            status = json.loads(status_path.read_text("utf-8"))
        print(json.dumps({"arm":mode,"phase":status.get("phase"),"calls":status.get("model_calls_started",0)},ensure_ascii=False),flush=True)
        if result.returncode:
            reason = "runner-exit-" + str(result.returncode)
            break
        if status.get("state") in {"done","blocked","paused"}:
            reason = status.get("phase")
            break
        if status.get("phase") == "provider-backoff":
            reason = "provider-backoff"
            break
    book = root/"books"/str(status.get("book_slug",""))
    chapters = []
    if (book/"progress.json").is_file():
        chapters = json.loads((book/"progress.json").read_text("utf-8"))["chapters"]
    complete = status.get("state") == "done" and bool(chapters) and all(c["status"]=="reviewed" for c in chapters)
    if complete:
        manifest = json.loads((book/"output"/"manifest.json").read_text("utf-8"))
        artifact = book/manifest["artifact_path"]
        complete = manifest["preview"] is False and hashlib.sha256(artifact.read_bytes()).hexdigest()==manifest["artifact_sha256"]
    metrics = status.get("execution_metrics",{})
    return {"arm":mode,"source_sha256":hashlib.sha256(source).hexdigest(),"workflow_revision":PIN,
            "state":status.get("state"),"stop_reason":reason,"wall_seconds":round(time.monotonic()-started,2),
            "calls_reserved":status.get("model_calls_started",0),"api":metrics.get("api",{}),
            "accepted_operations":metrics.get("accepted_operations",{}),
            "sections":len(chapters),"reviewed":sum(c["status"]=="reviewed" for c in chapters),
            "epub_verified":complete,"last_error_code":status.get("last_error_code")}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--cap",type=int,default=24,choices=range(1,41))
    args=p.parse_args()
    from omniroute import config
    for role in ("TRANSLATOR","REVIEWER","CORRECTOR"):
        config(role)
    with tempfile.TemporaryDirectory(prefix="coherent-auto-ab-") as temp:
        home=Path(temp)
        path=home/"synthetic.epub"
        make_epub(path)
        results=[run_arm(home,path.read_bytes(),mode,args.cap) for mode in ("off","on")]
    payload={"schema_version":1,"comparison":"live-paired-single-fixture","results":results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload,indent=2),flush=True)
    return 0 if all(x["epub_verified"] for x in results) else 1

if __name__ == "__main__":
    raise SystemExit(main())
