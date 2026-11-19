"""Single-process, two full public-domain chapter capacity check."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

from coherent_live_benchmark import run_arm

REPO="standardebooks/charles-dickens_great-expectations"
REV="a8de3e503018ef79147a06488b336fc5446fe7a4"
CHAPTERS=[(18,"12cc17481ab8f3352af4e1eff4b0b6f260a2781a"),
          (19,"3eb92d4882009ac9896c6eb961657d8acd6f6b9e")]

def chapter_bytes(n, expected):
    url=f"https://raw.githubusercontent.com/{REPO}/{REV}/src/epub/text/chapter-{n}.xhtml"
    with urlopen(url,timeout=30) as f:
        data=f.read(200000)
    if hashlib.sha1(f"blob {len(data)}\0".encode()+data).hexdigest()!=expected:
        raise ValueError("public domain fixture blob identity mismatch")
    return data

def chapter_text(data):
    root=ET.fromstring(data)
    ns="{http://www.w3.org/1999/xhtml}"
    paras=["".join(p.itertext()).strip() for p in root.findall(".//"+ns+"p")]
    if not paras or sum(len(p) for p in paras)<20000:
        raise ValueError("benchmark chapter too short")
    return [p for p in paras if p]

def make_epub(path,chapters):
    entries={"mimetype":"application/epub+zip",
             "META-INF/container.xml":'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'}
    items=[];spine=[];nav=[];lengths=[]
    for number,paras in chapters:
        name=f"chapter-{number}.xhtml"
        body="".join("<p>"+escape(p)+"</p>" for p in paras)
        entries["OEBPS/"+name]=f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter {number}</title></head><body><h1>Chapter {number}</h1>{body}</body></html>'
        items.append(f'<item id="c{number}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')
        nav.append(f'<li><a href="{name}">Chapter {number}</a></li>')
        lengths.append({"chapter":number,"source_characters":sum(map(len,paras)),"paragraphs":len(paras)})
    entries["OEBPS/content.opf"]='<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:benchmark:great-expectations-18-19</dc:identifier><dc:title>Two Chapters Capacity Benchmark</dc:title><dc:creator>Charles Dickens</dc:creator><dc:language>en</dc:language><meta property="dcterms:modified">2026-11-18T00:00:00Z</meta></metadata><manifest>'+''.join(items)+'<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine>'+''.join(spine)+'</spine></package>'
    entries["OEBPS/nav.xhtml"]='<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol>'+''.join(nav)+'</ol></nav></body></html>'
    with zipfile.ZipFile(path,"w") as z:
        for name,data in entries.items():
            info=zipfile.ZipInfo(name,(2026,11,18,0,0,0))
            info.compress_type=zipfile.ZIP_STORED if name=="mimetype" else zipfile.ZIP_DEFLATED
            z.writestr(info,data.encode("utf-8"))
    return lengths

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    from omniroute import config
    for role in ("TRANSLATOR","REVIEWER","CORRECTOR"):
        config(role)
    with tempfile.TemporaryDirectory(prefix="capacity-quality-") as temp:
        home=Path(temp)
        path=home/"input.epub"
        lengths=make_epub(path,[(n,chapter_text(chapter_bytes(n,sha))) for n,sha in CHAPTERS])
        print(json.dumps({"fixture":lengths,"upstream_revision":REV}),flush=True)
        row=run_arm(home,path.read_bytes(),"on",100,environment={
            "BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY":"on","BOOK_TRANSLATOR_COMPACT_REVIEW":"on",
            "BOOK_TRANSLATOR_CORRECTION_FEEDBACK":"on","BOOK_TRANSLATOR_SINGLE_REVIEW_GATE":"on",
            "BOOK_TRANSLATOR_PARAGRAPH_REVIEW":"on"},wall_budget_seconds=3600)
    passed=(row["sections"]==2 and row["reviewed"]==2 and row["epub_verified"]
            and row["wall_seconds"]<=3600)
    payload={"schema_version":1,"fixture":lengths,"upstream_revision":REV,
             "target_chapters_per_hour":2,"meets_capacity_target":passed,
             "reviewed_per_hour":round(row["reviewed"]*3600/max(1,row["wall_seconds"]),3),"result":row}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload,indent=2),flush=True)
    return 0 if passed else 1
if __name__=="__main__":
    raise SystemExit(main())
