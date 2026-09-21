"""Read-only, reproducible measurements of a supplied JAMT PDF/DOCX corpus.

DOCX page images are explicitly distinguished from editable manuscript sources.
The corpus itself is not bundled with the application.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree
import pymupdf as fitz


def analyze(root, output, render=False):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    documents, pages, fonts, sizes, baselines = [], [], Counter(), Counter(), Counter()
    hashes = {}
    hash_file = root / 'SHA256SUMS.txt'
    if hash_file.exists():
        for line in hash_file.read_text(encoding='utf-8-sig').splitlines():
            digest, name = line.split(maxsplit=1)
            path = (root / name).resolve()
            if root.resolve() not in path.parents:
                raise ValueError('Checksum path outside corpus')
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if not all(hashes.values()):
            raise ValueError('Corpus checksum mismatch')
    for pdf_path in sorted((root / 'PDF').glob('*.pdf')):
        word = root / 'Word' / (pdf_path.stem + '.docx')
        with ZipFile(word) as z:
            doc = etree.fromstring(z.read('word/document.xml'))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = ''.join(doc.xpath('//w:t/text()', namespaces=ns)).strip()
            word_info = dict(editable_characters=len(text), tables=len(doc.xpath('//w:tbl', namespaces=ns)),
                             drawings=len(doc.xpath('//w:drawing', namespaces=ns)),
                             image_only=not text, sha256=hashlib.sha256(word.read_bytes()).hexdigest())
        with fitz.open(pdf_path) as pdf:
            info = dict(id=pdf_path.stem, pages=len(pdf), page_size_pt=list(pdf[0].rect)[2:],
                        sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(), word=word_info)
            documents.append(info)
            for n, page in enumerate(pdf, 1):
                lines = [line for block in page.get_text('dict')['blocks'] if block['type'] == 0
                         for line in block['lines']]
                spans = [s for line in lines for s in line['spans'] if s['text'].strip()]
                for s in spans:
                    fonts[s['font']] += len(s['text'])
                    sizes[round(s['size'], 1)] += len(s['text'])
                body_lines = [line for line in lines if 85 < line['bbox'][1] < page.rect.height-70
                              and line['bbox'][2]-line['bbox'][0] > 170
                              and all(abs(s['size']-11) < .3 for s in line['spans'])]
                for a, b in zip(body_lines, body_lines[1:]):
                    delta = b['spans'][0]['origin'][1]-a['spans'][0]['origin'][1]
                    if abs(a['bbox'][0]-b['bbox'][0]) < 26 and 10 < delta < 18:
                        baselines[round(delta, 1)] += 1
                drawings = page.get_drawings()
                horizontal = [dict(y=round(item[1].y, 2), left=round(item[1].x, 2), right=round(item[2].x, 2), width=d['width'])
                              for d in drawings for item in d['items']
                              if item[0]=='l' and abs(item[1].y-item[2].y)<.15 and abs(item[1].x-item[2].x)>40]
                # Published Word/PDF tables often encode rules as filled narrow
                # rectangles, not stroked lines; d['width'] would report zero.
                horizontal.extend(dict(y=round(item[1].y0,2),left=round(item[1].x0,2),right=round(item[1].x1,2),
                    width=round(item[1].height,2),kind='filled_rectangle') for d in drawings if d.get('fill') is not None
                    for item in d['items'] if item[0]=='re' and item[1].width>40 and 0<item[1].height<4)
                page_text = page.get_text()
                record = dict(document=pdf_path.stem, page=n, fonts=dict(Counter(s['font'] for s in spans)),
                              images=len(page.get_image_info()), rules=horizontal,
                              has_table=bool(re.search(r'\bTable\s*\d|Таблица\s*\d', page_text)),
                              has_equation=bool(re.search(r'\(\d{1,2}\)', page_text)),
                              has_biography='Information about the authors' in page_text,
                              lines=[dict(text=''.join(s['text'] for s in line['spans']), bbox=line['bbox'],
                                          spans=[{k:s[k] for k in ('text','font','size','flags','origin')} for s in line['spans']])
                                     for line in lines])
                pages.append(record)
                if render:
                    dest=output/'pages'/pdf_path.stem;dest.mkdir(parents=True,exist_ok=True)
                    page.get_pixmap(dpi=120).save(dest/f'page-{n:02}.png')
    aggregate = dict(documents=documents, checksum_count=len(hashes), checksums_valid=all(hashes.values()),
                     pdf_pages=len(pages), image_only_word_files=sum(d['word']['image_only'] for d in documents),
                     font_characters=fonts.most_common(), size_characters=sizes.most_common(),
                     body_baseline_deltas_pt=baselines.most_common(),
                     table_pages=[(p['document'],p['page']) for p in pages if p['has_table']],
                     biography_pages=[(p['document'],p['page']) for p in pages if p['has_biography']])
    (output/'measurements.json').write_text(json.dumps(aggregate,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'page-evidence.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in aggregate.items() if k!='documents'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('corpus');parser.add_argument('output');parser.add_argument('--render',action='store_true')
    args=parser.parse_args();analyze(args.corpus,args.output,args.render)
