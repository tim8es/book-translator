from bs4 import BeautifulSoup, NavigableString, Tag
from pathlib import Path
import zipfile, re, json, xml.etree.ElementTree as ET
from urllib.parse import unquote

BLOCK = {'p','dt','dd','li','h1','h2','h3','h4','h5','h6','hr','pre'}
SKIP_CLASSES={'byline'}

def inline(node):
    if isinstance(node, NavigableString):
        return re.sub(r'\s+', ' ', str(node).replace('\xa0',' '))
    if not isinstance(node, Tag): return ''
    name=node.name.lower()
    if name=='br': return '\ue000'
    text=''.join(inline(c) for c in node.children)
    if name in ('em','i'):
        t=text.strip()
        return ('*'+t+'*') if t else ''
    if name in ('strong','b'):
        t=text.strip()
        return ('**'+t+'**') if t else ''
    return text

def norm(text):
    text=text.replace('\xa0',' ')
    text=text.replace('\ue000','\n')
    text=re.sub(r'[ \t\r\f\v]+',' ',text)
    text=re.sub(r' *\n *','\n',text)
    text=re.sub(r'\n{2,}','\n',text)
    return text.strip()

def convert(html):
    soup=BeautifulSoup(html,'lxml-xml')
    out=[]
    def emit(text, prefix=None):
        text=norm(text)
        if not text: return
        lines=[line.strip() for line in text.split('\n') if line.strip()]
        if prefix and len(lines)>1:
            out.append('\n'.join(prefix+line for line in lines))
        else:
            for line in lines:
                out.append((prefix or '')+line)
    def walk(node, quote=False):
        if isinstance(node, NavigableString): return
        if not isinstance(node, Tag): return
        name=node.name.lower()
        cls=set(node.get('class') or [])
        if cls & SKIP_CLASSES: return
        if name in ('script','style','svg','head'): return
        if name=='hr': out.append('---'); return
        if name in ('h1','h2','h3','h4','h5','h6'):
            emit('#'*int(name[1])+' '+norm(inline(node))); return
        if name=='dt': emit('**'+norm(inline(node))+'**'); return
        if name=='dd':
            t=norm(inline(node))
            if t: out.append(t)
            return
        if name=='li': emit(inline(node), '- '); return
        if name=='p': emit(inline(node), '> ' if quote else None); return
        if name=='pre': emit(inline(node), '> ' if quote else None); return
        if name=='blockquote':
            for c in node.children: walk(c, True)
            return
        for c in node.children: walk(c, quote)
    walk(soup.body)
    return '\n\n'.join(out).strip()+'\n'

def main():
    root=Path(__file__).resolve().parents[1]
    book=root/'books/good-intentions'
    epub=book/'source/Good_Intentions.epub'
    progress=json.loads((book/'progress.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(epub) as z:
        container=ET.fromstring(z.read('META-INF/container.xml'))
        rf=next(n.attrib['full-path'] for n in container.iter() if n.tag.endswith('rootfile'))
        opf=ET.fromstring(z.read(rf)); d=Path(rf).parent
        manifest={}
        for n in opf.iter():
            if n.tag.endswith('item') and n.attrib.get('id') and n.attrib.get('href'):
                manifest[n.attrib['id']] = (n.attrib['href'], n.attrib.get('media-type',''), n.attrib.get('properties',''))
        spine=[n.attrib['idref'] for n in opf.iter() if n.tag.endswith('itemref') and n.attrib.get('idref')]
        files=[]
        for idr in spine:
            href,mt,props=manifest.get(idr,('','',''))
            if 'nav' in props.split() or mt not in {'application/xhtml+xml','text/html'}:
                continue
            files.append((d/unquote(href.split('#',1)[0])).as_posix())
        if len(files) != len(progress['chapters']):
            raise SystemExit(f'spine/progress mismatch: {len(files)} vs {len(progress["chapters"])}')
        (book/'extracted').mkdir(parents=True, exist_ok=True)
        for rec,src_path in zip(progress['chapters'],files):
            text=convert(z.read(src_path).decode('utf-8'))
            (book/rec['source_path']).write_text(text,encoding='utf-8')

if __name__=='__main__':
    main()
