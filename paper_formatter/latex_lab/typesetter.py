"""Measured JAMT geometry and bounded layout plans compiled by XeLaTeX."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

from .bridge import escaped


STYLE_PATH = Path(__file__).resolve().parents[2] / 'apps/template_workspace/v2/styles/jamt.json'


def default_plan(blocks, *, running_footer=''):
    return {'body_leading': 12.65, 'front_gap': 8.0, 'caption_gap': 5.0,
            'table_size': 10.0, 'running_footer': running_footer, 'objects': {
                b.id: {'width': 'wide' if b.width_pt > 244 or b.columns >= 4 else 'column',
                       'scale': 1.0, 'break_before': False}
                for b in blocks if b.kind in {'figure', 'table'}}}


def validate_plan(patch, baseline, blocks):
    """No text, TeX, paths, arbitrary lengths, reordering or invented object IDs."""
    result = json.loads(json.dumps(baseline))
    accepted, rejected = [], []
    if not isinstance(patch, dict):
        return result, [], ['response_not_object']
    ranges = {'body_leading': (12.1, 13.2), 'front_gap': (6.0, 11.5),
              'caption_gap': (4.0, 7.0), 'table_size': (9.5, 10.0)}
    for key, value in patch.items():
        if key in ranges:
            low, high = ranges[key]
            if type(value) in {int, float} and low <= value <= high:
                result[key] = value; accepted.append(key)
            else:
                rejected.append(key)
        elif key != 'objects':
            rejected.append(key)
    by_id = {b.id: b for b in blocks}
    for block_id, changes in (patch.get('objects', {}) if isinstance(patch.get('objects', {}), dict) else {}).items():
        if block_id not in result['objects'] or not isinstance(changes, dict):
            rejected.append(block_id); continue
        block = by_id[block_id]
        for key, value in changes.items():
            valid = ((key == 'width' and isinstance(value, str) and value in {'column', 'wide'}
                      and (value == 'wide' or (block.columns < 4 and block.image_count <= 1)))
                     or (key == 'scale' and type(value) in {int, float} and 0.85 <= value <= 1.0)
                     or (key == 'break_before' and type(value) is bool))
            if valid:
                result['objects'][block_id][key] = value
                accepted.append(block_id + '.' + key)
            else:
                rejected.append(block_id + '.' + key)
    return result, accepted, rejected


def render(blocks, project, plan):
    project = Path(project)
    fonts = project / 'fonts'; fonts.mkdir(exist_ok=True)
    vendor = Path(__file__).resolve().parents[1] / 'vendor/fonts'
    for name in ('texgyretermes-math.otf', 'GUST-FONT-LICENSE.txt'):
        shutil.copy2(vendor / name, fonts / name)
    style = json.loads(STYLE_PATH.read_text(encoding='utf-8'))
    author = plan.get('running_footer', '')
    # Content from the current article only. Footer is journal furniture, excluded
    # from scientific text measurements; page numbering starts at one for the draft.
    author = re.sub(r'^\s*\d+\s*|\s*\d+\s*$', '', author).strip()
    if len(author) > 105:
        author = author[:102].rsplit(' ', 1)[0] + '…'
    preamble = r'''\documentclass[11pt,a4paper]{article}
\usepackage{fontspec}
\usepackage{polyglossia}
\setdefaultlanguage{english}
\setotherlanguage{russian}
\setmainfont{Liberation Serif}
\newfontfamily\cyrillicfont{Liberation Serif}
\usepackage{unicode-math}
\setmathfont{texgyretermes-math.otf}[Path=fonts/]
\usepackage[a4paper,left=18mm,right=18mm,top=30mm,bottom=24mm,headheight=14pt,headsep=28pt,footskip=24pt]{geometry}
\usepackage{microtype}
\usepackage{multicol,needspace,graphicx,adjustbox,array,booktabs,ragged2e,xcolor,fancyhdr,xurl,hyperref}
\hypersetup{hidelinks,pdfcreator={JAMT LaTeX laboratory},pdfproducer={XeLaTeX}}
\setlength{\columnsep}{6mm}
\setlength{\parindent}{7.5mm}
\setlength{\parskip}{0pt}
\setlength{\multicolsep}{8pt}
\setlength{\emergencystretch}{1.5em}
\tolerance=1400
\hbadness=2500
\widowpenalty=5000
\clubpenalty=5000
\raggedbottom
\pagestyle{fancy}
\fancyhf{}
\fancyhead[R]{\fontsize{9}{10}\selectfont\color{gray}Journal of Advanced Materials and Technologies.}
\fancyfoot[L]{\fontsize{9}{10}\selectfont __AUTHOR__}
\fancyfoot[R]{\fontsize{9}{10}\selectfont\thepage}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\footrulewidth}{0.3pt}
\newlength{\labtablewidth}
\newcommand{\labtablesize}{__TABLESIZE__}
\newcommand{\labtableleading}{__TABLELEADING__}
\newcommand{\labfigscale}{1.0}
\newcommand{\labimage}[2]{\adjustbox{max width=\labfigscale\linewidth,max height=0.65\textheight}{\includegraphics[width=#1pt]{#2}}}
\begin{document}
\fontsize{11}{__LEADING__}\selectfont
'''
    replacements = {'__AUTHOR__': escaped(author), '__TABLESIZE__': str(plan['table_size']),
                    '__TABLELEADING__': str(plan['table_size'] * 1.18), '__LEADING__': str(plan['body_leading'])}
    for key, value in replacements.items():
        preamble = preamble.replace(key, value)
    lines, columns, tail = [], False, False

    def switch(want_columns):
        nonlocal columns
        if want_columns == columns:
            return
        lines.append(r'\begin{multicols}{2}' if want_columns else r'\end{multicols}')
        columns = want_columns

    def paragraph(block, caption=False):
        role = 'figure_caption' if caption else block.role
        spec = style['roles'].get(role, style['roles']['body'])
        pt = spec.get('pt', 11)
        before = spec.get('before', 0)
        after = spec.get('after', 0)
        if block.zone == 'front_matter':
            after = min(after, plan['front_gap'])
        if caption:
            before = after = plan['caption_gap']
        align = {'center': r'\centering', 'right': r'\raggedleft', 'left': r'\RaggedRight', 'both': r'\justifying'}.get(spec.get('align'), r'\justifying')
        is_heading = role.startswith('heading_') or role.endswith('_heading') or role == 'author_information'
        if is_heading:
            lines.append(r'\needspace{4\baselineskip}')
        lines.append('% ' + block.id + ' ' + role)
        if before:
            lines.append(rf'\par\addvspace{{{before}pt}}')
        leading = plan['body_leading'] if pt == 11 and role == 'body' else pt * 1.15
        indent = spec.get('first_indent', 0)
        text = block.tex
        if role == 'editorial_metadata':
            text = text.replace(r'\quad ', r'\hfill ')
        language = 'russian' if len(re.findall('[А-Яа-яЁё]', block.text)) > len(re.findall('[A-Za-z]', block.text)) else 'english'
        # Avoid a small initial label stranded on its own line.
        if len(block.text.strip()) < 35 and role in {'abstract', 'keywords'}:
            lines.append(r'\needspace{3\baselineskip}')
        lines.append(r'{\selectlanguage{' + language + r'}\fontsize{' + str(pt) + '}{' + str(leading) + r'}\selectfont ' + align
                     + rf'\setlength{{\parindent}}{{{indent}pt}}'
                     + (r'\bfseries ' if spec.get('bold') else '')
                     + (r'\itshape ' if spec.get('italic') else '')
                     + (r'\noindent ' if not indent else '') + text + r'\par}')
        if after:
            lines.append(rf'\addvspace{{{after}pt}}')
        if is_heading:
            lines.append(r'\nobreak')

    for block in blocks:
        if block.role in {'author_information', 'received_metadata', 'copyright_metadata'}:
            tail = True
        front = block.zone == 'front_matter'
        obj = plan['objects'].get(block.id)
        wide = bool(obj and obj['width'] == 'wide')
        switch(not front and not tail and not wide)
        if obj:
            if obj['break_before']:
                lines.append(r'\newpage')
            lines += ['% ' + block.id + ' ' + block.kind,
                      r'\par\addvspace{6pt}\noindent\begin{minipage}{\linewidth}',
                      rf'\renewcommand{{\labfigscale}}{{{obj["scale"]}}}\centering']
            if block.kind == 'table':
                for caption in block.captions:
                    paragraph(_as_block(caption), caption=True)
            # Scale the actual requested image dimensions, not just a maximum
            # width (a smaller image otherwise ignores the planner's scale).
            object_tex = re.sub(r'\\labimage\{([\d.]+)\}',
                lambda match: r'\labimage{' + f'{float(match[1])*obj["scale"]:.3f}' + '}', block.tex)
            lines.append(object_tex)
            if block.kind != 'table':
                for caption in block.captions:
                    paragraph(_as_block(caption), caption=True)
            lines += [r'\end{minipage}\par\addvspace{6pt}']
        elif block.kind == 'equation':
            lines.append('% ' + block.id + ' equation')
            math = block.tex
            if math.startswith(r'\(') and math.endswith(r'\)'):
                math = math[2:-2]
            lines.append(r'\[\displaystyle ' + math + r'\]')
        else:
            paragraph(block)
    switch(False)
    source = preamble + '\n'.join(lines) + '\n\\end{document}\n'
    (project / 'main.tex').write_text(source, encoding='utf-8')
    (project / 'layout_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    return project / 'main.tex'


def _as_block(value):
    from .bridge import Block
    return Block(**value)


def compile_pdf(project, timeout=180):
    project = Path(project).resolve()
    engine = shutil.which('xelatex')
    if not engine:
        raise RuntimeError('XeLaTeX is required for the experimental exporter.')
    command = [engine, '-no-shell-escape', '-halt-on-error', '-interaction=nonstopmode', '-file-line-error', 'main.tex']
    logs = []
    for _ in range(2):
        env = dict(os.environ, openin_any='p', openout_any='p')
        result = subprocess.run(command, cwd=project, capture_output=True, timeout=timeout, env=env)
        logs.append(result.stdout.decode('utf-8', errors='replace'))
        (project / 'compile.stdout.log').write_text('\n'.join(logs), encoding='utf-8')
        if result.returncode:
            raise RuntimeError('XeLaTeX failed; see compile.stdout.log: ' + logs[-1][-1600:])
    log = (project / 'main.log').read_text(encoding='utf-8', errors='replace')
    report = {'engine': Path(engine).name,
              'overfull_hbox_pt': [float(v) for v in re.findall(r'Overfull \\hbox \(([\d.]+)pt too wide\)', log)],
              'overfull_vbox_pt': [float(v) for v in re.findall(r'Overfull \\vbox \(([\d.]+)pt too high\)', log)],
              'missing_glyphs': list(dict.fromkeys(re.findall(r'Missing character:.*', log))),
              'underfull_boxes': log.count('Underfull \\hbox')}
    (project / 'compile_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if report['missing_glyphs']:
        raise RuntimeError('Missing font glyphs; PDF is not eligible for promotion.')
    return project / 'main.pdf', report
