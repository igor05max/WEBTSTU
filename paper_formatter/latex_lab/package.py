"""Package measured experiments for a private, portable side-by-side review."""
import argparse
from hashlib import sha256
import html
import json
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

from .quality import gate, measure, render_pages
from .typesetter import compile_pdf


def package(root, output, cases):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summary = []
    for slug, title in cases:
        folder = root/slug
        report = json.loads((folder/'comparison.json').read_text(encoding='utf-8'))
        selected = report.get('selected_project', 'latex')
        if selected not in {'latex', 'latex_qwen'}:
            raise ValueError('Unexpected selected project.')
        project = folder/selected
        manifest = json.loads((folder/'latex/manifest.json').read_text(encoding='utf-8'))
        # Recompile with the final compiler contract, then validate the exact file
        # that is packaged; stale previews are never used as deliverables.
        pdf, compilation = compile_pdf(project)
        metrics = measure(pdf, manifest, compilation)
        result_gate = gate(metrics, report['native'])
        if not result_gate['passed']:
            raise ValueError(f'{slug}: selected PDF failed: {result_gate["errors"]}')
        files = {'word_pdf': slug+'-word.pdf', 'latex_pdf': slug+'-latex.pdf',
                 'docx': slug+'-word.docx', 'tex_zip': slug+'-latex.zip'}
        for source, dest in [(folder/'native/result.pdf',files['word_pdf']),
                             (folder/'native/result.docx',files['docx']), (pdf,files['latex_pdf'])]:
            shutil.copy2(source, output/dest)
        # Raster previews also work in browsers without an embedded PDF viewer.
        previews = {'native': f'previews/{slug}/word', 'latex': f'previews/{slug}/latex'}
        for side, name in [('native','word_pdf'),('latex','latex_pdf')]:
            render_pages(output/files[name], output/previews[side])
        with ZipFile(output/files['tex_zip'],'w',ZIP_DEFLATED) as archive:
            for source in sorted(project.rglob('*')):
                relative = source.relative_to(project)
                if source.is_file() and (relative.parts[0] in {'assets','fonts'} or source.name in {'main.tex','layout_plan.json'}):
                    archive.write(source, relative.as_posix())
            archive.writestr('manifest.json', json.dumps(manifest,ensure_ascii=False,indent=2))
            archive.writestr('README.txt', 'Build with XeLaTeX twice: xelatex -no-shell-escape main.tex\n'
                'Requires Liberation Serif, DejaVu Sans, and TeX Live packages listed in main.tex.\n'
                'Original image bytes are included; transformed crops are separate assets.\n'
                'The DOCX companion uses native Word pagination, not this LaTeX layout.\n')
        qwen = json.loads((folder/'qwen_comparison.json').read_text(encoding='utf-8')) if (folder/'qwen_comparison.json').exists() else {}
        entry = {'id':slug,'title':title,'files':files,'previews':previews,'native_pages':report['native']['pages'],
                 'latex_pages':metrics['pages'],'native_sparse_pages':report['native'].get('sparse_pages',[]),
                 'latex_sparse_pages':metrics['sparse_pages'],'pdf_word_coverage':metrics['word_coverage'],
                 'text_transfer':manifest['text_transfer'],'equations':len(manifest['formulas']),
                 'image_uses':len(manifest['assets']),'image_originals_preserved':all(
                     sha256((folder/'latex'/a['path']).read_bytes()).hexdigest()==a['sha256'] for a in manifest['assets']),
                 'selected_project':selected,'selected_plan':json.loads((project/'layout_plan.json').read_text()),
                 'compile':compilation,'qwen':qwen,
                 'sha256':{name:sha256((output/path).read_bytes()).hexdigest() for name,path in files.items()},
                 'agent_visual_review':{'status':'pending','human_review':False}}
        summary.append(entry)
        (output/(slug+'-metrics.json')).write_text(json.dumps({'final':entry,'experiment':report},ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    write_html(output, summary)
    return summary


def write_html(output, cases):
    options = ''.join('<option value="'+html.escape(c['id'])+'">'+html.escape(c['title'])+'</option>' for c in cases)
    data = json.dumps(cases,ensure_ascii=False).replace('</','<\\/')
    page = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сравнение Word и LaTeX — JAMT</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f6;color:#18212a;font:16px/1.5 system-ui,sans-serif}
header,main{max-width:1700px;margin:auto;padding:24px 30px}header{background:#fff;border-bottom:1px solid #dce1e5}
h1{font-size:27px;margin:0 0 8px}p{max-width:1080px;margin:8px 0;color:#455360}select,a{font:inherit}
select{padding:9px 12px;max-width:100%;border:1px solid #bac5ce;border-radius:5px;background:white}
.facts{display:flex;gap:24px;flex-wrap:wrap;margin:18px 0}.fact strong{display:block;font-size:23px;color:#152f43}.fact span{color:#536371}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:20px}.panel{background:#fff;border:1px solid #d7dfe4;border-radius:6px;overflow:hidden}
.bar{padding:12px 16px;display:flex;align-items:center;justify-content:space-between;gap:10px}h2{font-size:18px;margin:0}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;border-top:1px solid #d7dfe4;font-size:14px}
.controls button,.controls input,.controls select{font:inherit;padding:5px 8px;border:1px solid #bac5ce;border-radius:4px;background:white}
.controls input{width:60px}.viewport{height:76vh;min-height:550px;overflow:auto;background:#e4e8ec;padding:12px;border-top:1px solid #d7dfe4}
.viewport img{display:block;width:100%;max-width:none;height:auto;margin:0 auto;box-shadow:0 1px 5px #0002}
a{color:#165c8e}nav{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0}.note{background:#e9eef2;padding:13px 16px;border-radius:5px;font-size:14px}
details{margin:18px 0;background:white;padding:14px 18px;border:1px solid #d7dfe4;border-radius:5px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
@media(max-width:850px){header,main{padding:18px}.panels{grid-template-columns:1fr}.viewport{height:70vh;min-height:450px}.facts{gap:16px}}
</style><header><h1>Сравнение Word и LaTeX</h1>
<p>Эксперимент с оформлением JAMT. Одна и та же статья, два способа сборки PDF. Страницы можно прокручивать независимо.</p>
<label for="case">Статья </label><select id="case">__OPTIONS__</select></header><main>
<div class="facts"><div class="fact"><strong id="pages"></strong><span>страниц Word → LaTeX</span></div>
<div class="fact"><strong id="content"></strong><span>текстовых фрагментов передано без потерь</span></div>
<div class="fact"><strong id="objects"></strong><span>формул · изображений</span></div>
<div class="fact"><strong id="sparse"></strong><span>почти пустых страниц Word → LaTeX</span></div></div>
<div class="note">Количество страниц — только один из показателей. Смотрите читаемость формул, таблиц и подписей.
DOCX сохраняет вёрстку Word: его страницы могут отличаться от LaTeX PDF. AI-проверка выборочная; это экспериментальный режим.</div>
<nav><a id="word" download>Скачать DOCX</a><a id="tex" download>Скачать LaTeX-проект</a><a id="metrics" download>Показатели и протокол</a></nav>
<p id="review"></p><div class="panels">__PANELS__</div>
<details><summary>Что проверял Qwen</summary><p>Сравнение A/B с чередованием порядка. Модель не знает названий движков.
Её замечания являются дополнительным мнением, а не доказательством потери текста или правильности исследования.</p><pre id="ai"></pre></details>
<details><summary>Параметры выбранного варианта</summary><pre id="plan"></pre></details>
</main><script>const cases=__DATA__; const el=id=>document.getElementById(id);
const state={native:1,latex:1};let current;
function page(side){const count=side==='native'?current.native_pages:current.latex_pages;
state[side]=Math.max(1,Math.min(count,state[side]));el(side+'Page').value=state[side];el(side+'Page').max=count;
el(side+'Count').textContent='/ '+count;el(side+'Prev').disabled=state[side]===1;el(side+'Next').disabled=state[side]===count;
el(side).src=current.previews[side]+'/page-'+String(state[side]).padStart(2,'0')+'.png';
el(side).alt=(side==='native'?'Word':'LaTeX')+', страница '+state[side];el(side+'View').scrollTop=0;}
function show(){const c=cases.find(x=>x.id===el('case').value);
current=c;state.native=state.latex=1;
el('pages').textContent=c.native_pages+' → '+c.latex_pages;
el('content').textContent=c.text_transfer.emitted_nodes+' / '+c.text_transfer.expected_nodes;
el('objects').textContent=c.equations+' · '+c.image_uses;
el('sparse').textContent=c.native_sparse_pages.length+' → '+c.latex_sparse_pages.length;
page('native');page('latex');
el('nativeLink').href=c.files.word_pdf;el('latexLink').href=c.files.latex_pdf;el('word').href=c.files.docx;el('tex').href=c.files.tex_zip;el('metrics').href=c.id+'-metrics.json';
el('ai').textContent=JSON.stringify(c.qwen,null,2);el('plan').textContent=JSON.stringify(c.selected_plan,null,2);
el('review').textContent=c.agent_visual_review?.status==='completed'?'Codex просмотрел все '+c.latex_pages+' страниц LaTeX. '+c.agent_visual_review.note:'Полный визуальный просмотр ещё не отмечен.';}
for(const side of ['native','latex']){
el(side+'Prev').addEventListener('click',()=>{state[side]--;page(side)});
el(side+'Next').addEventListener('click',()=>{state[side]++;page(side)});
el(side+'Page').addEventListener('change',()=>{state[side]=Number(el(side+'Page').value)||1;page(side)});
el(side+'Zoom').addEventListener('change',()=>{el(side).style.width=el(side+'Zoom').value+'%'});}
el('case').addEventListener('change',show);show();</script></html>'''
    panels = ''.join(f'''<section class="panel"><div class="bar"><h2>{title}</h2><a id="{side}Link" target="_blank">Открыть PDF</a></div>
<div class="controls"><button id="{side}Prev" aria-label="Предыдущая страница {title}">←</button><label>Страница <input id="{side}Page" type="number" min="1" value="1" aria-label="Страница {title}"></label><span id="{side}Count"></span>
<button id="{side}Next" aria-label="Следующая страница {title}">→</button><label>Масштаб <select id="{side}Zoom" aria-label="Масштаб {title}"><option value="100">По ширине</option><option value="150">150%</option><option value="200">200%</option></select></label></div>
<div class="viewport" id="{side}View"><img id="{side}" alt="Страница PDF"></div></section>'''
        for side,title in [('native','Текущий Word'),('latex','Эксперимент LaTeX')])
    (Path(output)/'index.html').write_text(page.replace('__OPTIONS__',options).replace('__DATA__',data).replace('__PANELS__',panels),encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root',type=Path);parser.add_argument('output',type=Path)
    args = parser.parse_args()
    summary = package(args.root,args.output,[('balabanov','Балабанов — формулы, таблицы и графики'),
                                            ('tyutyunnik','Тютюнник — иллюстрированная статья')])
    print(json.dumps([{k:c[k] for k in ['id','native_pages','latex_pages','text_transfer','equations','image_uses']} for c in summary],ensure_ascii=False))
