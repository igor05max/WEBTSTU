"""Coordinate evidence for front metadata and recurring journal details.

Select five articles from each issue, measure all their pages and render
first/closing/table evidence. Source PDFs remain untouched.
"""
from collections import defaultdict, Counter
from hashlib import sha256
import argparse
import json
from pathlib import Path
import re
import statistics

import pymupdf
from PIL import Image, ImageDraw


def lines(page):
    result=[]
    for block in page.get_text('rawdict')['blocks']:
        if block['type'] != 0: continue
        for line in block['lines']:
            chars=[c for span in line['spans'] for c in span['chars']]
            text=''.join(c['c'] for c in chars)
            visible=[c for c in chars if c['c'].strip()]
            if not visible: continue
            bounds=pymupdf.Rect(visible[0]['bbox'])
            for char in visible[1:]:bounds.include_rect(char['bbox'])
            result.append({'text':text.strip(),'bbox':[round(v,3) for v in bounds],
                           'baseline':round(line['spans'][0]['origin'][1],3),
                           'spans':[{'text':''.join(c['c'] for c in span['chars']).strip(),
                                     'font':span['font'],'size':round(span['size'],2),
                                     'baseline':round(span['origin'][1],3)} for span in line['spans']]})
    return result


def audit(corpus, output):
    groups=defaultdict(list)
    for path in sorted((corpus/'PDF').glob('*.pdf')):
        with pymupdf.open(path) as document:
            match=re.search(r'jamt\.2024\.(\d{2})\.pp',document[0].get_text())
            if match:groups[match[1]].append(path)
    selected=[]
    for issue, paths in sorted(groups.items()):
        assert len(paths)>=5, issue
        selected.extend(paths[round(i*(len(paths)-1)/4)] for i in range(5))
    assert len(selected)==20 and len(set(selected))==20
    output.mkdir(parents=True,exist_ok=True)
    records=[];visuals=defaultdict(list)
    for path in selected:
        with pymupdf.open(path) as doc:
            all_pages=[lines(page) for page in doc]
            first=all_pages[0]
            doi=next(line for line in first if line['text'].startswith('DOI:'))
            udc=next(line for line in first if re.match(r'^(УДК|UDC)\b',line['text']))
            examples={'front':0,'closing':len(doc)-1}
            table_page=next((i for i,p in enumerate(all_pages) if any(re.match(r'^(Table|Таблица)\s*\d',x['text']) for x in p)),None)
            if table_page is not None:examples['table']=table_page
            record={'document':path.name,'sha256':sha256(path.read_bytes()).hexdigest(),'pages':len(doc),
                    'doi':doi,'udc':udc,'doi_udc_same_baseline':abs(doi['baseline']-udc['baseline'])<.5,
                    'front_top':[x for x in first if x['bbox'][1]<180],
                    'closing_metadata':[x for page in all_pages[-2:] for x in page if re.search(r'^(Received|Accepted|Published|Поступила|Принята|Опубликована)',x['text'])],
                    'captions':[dict(page=i+1,**x) for i,page in enumerate(all_pages) for x in page if re.match(r'^(Fig\.|Рис\.|Table|Таблица)\s*\d',x['text'])],
                    'evidence_pages':{key:value+1 for key,value in examples.items()}}
            records.append(record)
            dest=output/'pages'/path.stem;dest.mkdir(parents=True,exist_ok=True)
            for kind,n in examples.items():
                target=dest/f'{kind}-{n+1:02}.png'
                doc[n].get_pixmap(dpi=120).save(target)
                visuals[kind].append((path.stem,n+1,target))
    for kind,images in visuals.items():
        for start in range(0,len(images),2):
            sheet=Image.new('RGB',(2000,1440),'#f0f1f3');draw=ImageDraw.Draw(sheet)
            for index,(name,page,path) in enumerate(images[start:start+2]):
                with Image.open(path) as picture:
                    picture.thumbnail((990,1395))
                    sheet.paste(picture,(index*1000,35))
                draw.text((index*1000+10,10),f'{name} - page {page}',fill='black')
            sheet.save(output/f'{kind}-{start//2+1:02}.png')
    summary={'articles':len(records),'pages':sum(x['pages'] for x in records),
             'doi_udc_same_baseline':sum(x['doi_udc_same_baseline'] for x in records),
             'doi_right_edge_pt':{'min':min(x['doi']['bbox'][2] for x in records),'max':max(x['doi']['bbox'][2] for x in records)},
             'udc_left_edge_pt':{'min':min(x['udc']['bbox'][0] for x in records),'max':max(x['udc']['bbox'][0] for x in records)},
             'doi_sizes':dict(Counter(s['size'] for x in records for s in x['doi']['spans'] if s['text'])),
             'issue_selection':{issue:5 for issue in sorted(groups)}}
    (output/'audit.json').write_text(json.dumps({'summary':summary,'articles':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('corpus',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();audit(args.corpus,args.output)
