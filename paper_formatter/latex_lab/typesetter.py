"""Measured JAMT geometry and bounded layout plans compiled by XeLaTeX."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

from .bridge import escaped
from .master_template import load_style, write_template_files
from apps.template_workspace.v2.styles.identifiers import identifier_kind


def default_plan(blocks, *, running_footer='', reference_layout=False, running_header='', page_start=1):
    style = load_style()
    return {**{key: style['latex'][key] for key in ('body_leading', 'front_gap', 'caption_gap', 'table_size')},
            'running_footer': running_footer, 'reference_layout': reference_layout,
            'running_header': running_header, 'page_start': page_start, 'objects': {
                b.id: {'width': 'wide' if b.width_pt > 244 or b.columns >= 4 or b.breakable else 'column',
                       'scale': 1.0, 'break_before': False}
                for b in blocks if b.kind in {'figure', 'table'}}}


def validate_plan(patch, baseline, blocks):
    """No text, TeX, paths, arbitrary lengths, reordering or invented object IDs."""
    result = json.loads(json.dumps(baseline))
    accepted, rejected = [], []
    if not isinstance(patch, dict):
        return result, [], ['response_not_object']
    ranges = load_style()['latex']['layout_ranges']
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
                      and (value == 'wide' or (block.columns < 4 and block.image_count <= 1 and not block.breakable)))
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
    contract = write_template_files(project)
    style = load_style()
    plan['template'] = {'version': contract['version'], 'profile_sha256': contract['profile_sha256']}
    author = plan.get('running_footer', '')
    # Content from the current article only. Footer is journal furniture, excluded
    # from scientific text measurements; page numbering starts at one for the draft.
    author = re.sub(r'^\s*\d+\s*|\s*\d+\s*$', '', author).strip()
    if len(author) > 105:
        author = author[:102].rsplit(' ', 1)[0] + '…'
    header = plan.get('running_header') or style['journal']
    first_page = max(1, int(plan.get('page_start', 1)))
    preamble = (
        r"\documentclass{jamt}" + "\n"
        + r"\renewcommand{\jamtjournal}{" + escaped(header) + "}\n"
        + r"\renewcommand{\jamtauthors}{" + escaped(author) + "}\n"
        + r"\renewcommand{\labtablesize}{" + str(plan['table_size']) + "}\n"
        + r"\renewcommand{\labtableleading}{" + str(plan['table_size'] * 1.15) + "}\n"
        + r"\begin{document}" + "\n"
        + r"\setcounter{page}{" + str(first_page) + "}\n"
        + r"\fontsize{\JAMTBodySize}{" + str(plan['body_leading']) + r"}\selectfont" + "\n")
    lines, columns, tail = [], False, False

    # Native Word floats may be anchored after a short editorial paragraph but
    # appear above it on the page. Float only the intact wide figure groups;
    # prose order and all scientific content remain unchanged.
    blocks = list(blocks)
    if len(blocks) >= 2 and blocks[0].role == 'article_type' and blocks[1].role == 'rubric':
        lines.append(r'\JAMTFrontStart')
    moves = []
    if plan.get('reference_layout'):
        i = 0
        while i < len(blocks):
            if blocks[i].kind != 'figure' or blocks[i].source_columns != 1:
                i += 1; continue
            end = i
            while end < len(blocks) and blocks[end].kind == 'figure' and blocks[end].source_columns == 1:
                end += 1
            start = i-1
            while start >= 0 and blocks[start].role in {'funding_text', 'acknowledgements_text', 'conflict_text'}:
                start -= 1
            if start >= 0 and start < i-1 and blocks[start].role in {'funding_heading', 'acknowledgements_heading', 'conflict_heading'} and sum(len(b.text) for b in blocks[start:i]) < 600:
                moves.append({'figures': [b.id for b in blocks[i:end]], 'before': blocks[start].id,
                              'reason': 'keep_short_editorial_section_together_below_wide_figures'})
                blocks[start:end] = blocks[i:end] + blocks[start:i]
            i = end
    plan['layout_actions'] = moves

    def switch(want_columns):
        nonlocal columns
        if want_columns == columns:
            return
        lines.append(r'\begin{multicols}{\JAMTColumns}' if want_columns else r'\end{multicols}')
        columns = want_columns

    previous_paragraph_heading = False
    def paragraph(block, caption=False):
        nonlocal previous_paragraph_heading
        role = 'figure_caption' if caption else block.role
        identifier = identifier_kind(block.text) if role == 'editorial_metadata' and block.zone == 'front_matter' else ''
        if identifier == 'doi':
            role = 'doi_metadata'
        spec = style['roles'].get(role, style['roles']['body'])
        pt = spec.get('pt', 11)
        faithful = plan.get('reference_layout', False)
        before = block.space_before if faithful else spec.get('before', 0)
        after = block.space_after if faithful else spec.get('after', 0)
        if role in {'editorial_metadata', 'doi_metadata'} and ('[MISSING:' in block.text or '[НЕ УКАЗАНО:' in block.text):
            after = 4
        if role == 'received_metadata':
            position = next((i for i, b in enumerate(blocks) if b.id == block.id), 0)
            before = 0 if position and blocks[position-1].role == role else 11.5
            after = 0
        if block.zone == 'front_matter' and not faithful:
            after = min(after, plan['front_gap'])
        if block.zone == 'front_matter' and ('[MISSING:' in block.text or '[НЕ УКАЗАНО:' in block.text):
            before,after = min(before,6),min(after,6)
        if caption and not faithful:
            before = after = plan['caption_gap']
        is_heading = role.startswith('heading_') or role.endswith('_heading') or role == 'author_information'
        if is_heading:
            lines.append(r'\needspace{4\baselineskip}')
        elif role in {'body', 'reference_item'} and not previous_paragraph_heading:
            lines.append(r'\needspace{2\baselineskip}')
        lines.append('% ' + block.id + ' ' + role)
        lines.append(r'\par')
        leading = plan['body_leading'] if pt == 11 and role in {'body', 'funding_text', 'acknowledgements_text', 'conflict_text'} else pt * 1.15
        indent = spec.get('first_indent', 0)
        text = block.tex
        hanging = ''
        if block.list_label:
            label=escaped(block.list_label)
            if text.startswith(label+' '):
                text=r'\makebox[21.3pt][l]{'+label+'}'+text[len(label)+1:]
                hanging=r'\hangindent=21.3pt\hangafter=1 '
                indent=0
        if role == 'rubric':
            # A source textbox's centring belongs to its own first line.
            text = r'\par\noindent '.join(text.split(r'\newline '))
        if role == 'copyright_metadata':
            logo = re.search(r'\\labimage\{[\d.]+\}\{[^}]+\}', text)
            if logo:
                text_without_logo = text[:logo.start()] + text[logo.end():]
                lines.append(r'\jamtlicense{' + logo[0] + '}{' + text_without_logo + '}')
                return
        if role == 'editorial_metadata':
            if identifier == 'pair' and r'\quad ' in text:
                left, right = text.split(r'\quad ', 1)
                # A source hyperlink or emphasis group may cross the tab. Do
                # not split its TeX braces; ordinary glue retains that group.
                depth = sum(1 if m[0] == '{' else -1 for m in re.finditer(r'(?<!\\)[{}]', left))
                text = (r'\JAMTIdentifierLine{' + left + '}{' + right.replace(r'\quad ', ' ') + '}'
                        if depth == 0 else text.replace(r'\quad ', r'\hfill '))
            else:
                text = text.replace(r'\quad ', r'\hfill ')
        language = 'russian' if len(re.findall('[А-Яа-яЁё]', block.text)) > len(re.findall('[A-Za-z]', block.text)) else 'english'
        # Avoid a small initial label stranded on its own line.
        if len(block.text.strip()) < 35 and role in {'abstract', 'keywords'}:
            lines.append(r'\needspace{3\baselineskip}')
        template_role = role if role in style['roles'] else 'body'
        options = f'language={language},before={before},after={after},leading={leading},indent={indent}'
        if is_heading:
            options += ',keepnext=true'
        lines.append(r'\JAMTParagraph[' + options + ']{' + template_role + '}{' + hanging + text + '}')
        if role == 'citation' and language == 'english':
            lines.append(r'\JAMTFrontDivider')
        if is_heading:
            lines.append(r'\nobreak')
        previous_paragraph_heading = is_heading
        if role == 'received_metadata':
            position = next((i for i, b in enumerate(blocks) if b.id == block.id), 0)
            if position+1<len(blocks) and blocks[position+1].role == role:
                lines.append(r'\nobreak')

    for block in blocks:
        if block.role in {'author_information', 'author_bio', 'received_metadata', 'copyright_metadata'}:
            tail = True
        front = block.zone == 'front_matter'
        obj = plan['objects'].get(block.id)
        wide = bool(obj and obj['width'] == 'wide')
        if plan.get('reference_layout') and block.source_columns:
            wide = block.source_columns == 1
            if obj:
                obj = dict(obj, width='wide' if wide else 'column')
        if block.breakable or (block.kind == 'table' and block.columns >= 4):
            wide = True
        want_columns = not front and not tail and not wide
        # A tall first picture can exceed BOTH short columns below a wide table.
        # Measure the complete picture + captions at the actual column width
        # before entering multicols, and reserve that height on the outer page.
        # Otherwise multicols may ship an overfull minipage through the footer.
        reserve_object = bool(want_columns and not columns and obj and not block.breakable)
        if reserve_object:
            lines.append(r'\setbox\jamtobjectbox=\vbox\bgroup\hsize=\dimexpr(\textwidth-\columnsep)/2\relax\linewidth=\hsize\columnwidth=\hsize')
        else:
            switch(want_columns)
        if obj:
            if block.breakable:
                # longtable owns page breaking and repeated headers; never put
                # it inside a minipage or the multicols output routine.
                lines.append(r'\Needspace{8\baselineskip}')
                caption_start = len(lines)
                for caption in block.captions:paragraph(_as_block(caption))
                caption_tex = '\n'.join(lines[caption_start:])
                del lines[caption_start:]
                table_tex = block.tex
                if caption_tex:
                    # The caption belongs to the first-page head. An external
                    # paragraph can be moved by longtable's output routine and
                    # strand a rule/header before it when the page is nearly full.
                    caption_row = (r'\multicolumn{' + str(block.columns) + r'}{@{}p{\linewidth}@{}}{'
                                   + r'\begin{minipage}{\linewidth}' + caption_tex
                                   + r'\end{minipage}}\\[4pt]' + '\n')
                    table_tex = table_tex.replace(r'\toprule', caption_row + r'\toprule', 1)
                lines.extend(['% '+block.id+' multipage table',table_tex])
                continue
            if obj['break_before']:
                lines.append(r'\newpage')
            gap = max(6,block.space_before) if plan.get('reference_layout') else 6
            lines += ['% ' + block.id + ' ' + block.kind,
                      rf'\par\addvspace{{{gap}pt}}\noindent\begin{{minipage}}{{\linewidth}}',
                      rf'\renewcommand{{\labfigscale}}{{{obj["scale"]}}}\centering']
            if block.kind == 'table':
                for caption in block.captions:
                    paragraph(_as_block(caption))
            # Scale the actual requested image dimensions, not just a maximum
            # width (a smaller image otherwise ignores the planner's scale).
            object_tex = re.sub(r'\\labimage\{([\d.]+)\}',
                lambda match: r'\labimage{' + f'{float(match[1])*obj["scale"]:.3f}' + '}', block.tex)
            if block.kind == 'figure' and block.role == 'figure_caption':
                # Native floating pictures can share a paragraph with their
                # caption. In a semantic figure the caption starts BELOW the
                # complete image group, never to the right of its last image.
                pictures = list(re.finditer(r'\\labimage\{[\d.]+\}\{[^}]+\}', object_tex))
                if pictures:
                    end = pictures[-1].end()
                    object_tex = (object_tex[:end] + r'\par\vspace{4pt}{\fontsize{10}{11.5}\selectfont '
                                  + object_tex[end:] + r'\par}')
            lines.append(object_tex)
            if block.kind != 'table':
                for caption in block.captions:
                    paragraph(_as_block(caption), caption=True)
            gap = max(6,block.space_after) if plan.get('reference_layout') else 6
            lines += [rf'\end{{minipage}}\par\addvspace{{{gap}pt}}']
            if reserve_object:
                lines.extend([r'\egroup',
                              r'\Needspace{\dimexpr\ht\jamtobjectbox+\dp\jamtobjectbox+2\multicolsep+\baselineskip\relax}'])
                switch(True)
                lines.append(r'\noindent\box\jamtobjectbox\par')
        elif block.kind == 'equation':
            lines.append('% ' + block.id + ' equation')
            math = block.tex
            if math.startswith(r'\(') and math.endswith(r'\)'):
                math = math[2:-2]
            if block.equation_number:
                lines.append(r'\begin{equation*}\displaystyle ' + math + r'\tag*{' + escaped(block.equation_number) + r'}\end{equation*}')
            else:
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


def compile_report(log, engine='xelatex'):
    """Keep final overflow warnings; inventory rejected multicol trials apart."""
    vertical = r'Overfull \\vbox \(([\d.]+)pt too high\)'
    discarded = []
    def trial(match):
        section = match.group()
        if section.endswith('JAMT-BALANCE-DISCARDED'):
            discarded.extend(float(v) for v in re.findall(vertical, section))
            return re.sub(vertical, 'Discarded balancing trial', section)
        return section
    # Fail closed for incomplete, nested or otherwise unrecognised markers.
    checked = re.sub(r'JAMT-BALANCE-BEGIN(?:(?!JAMT-BALANCE-).)*JAMT-BALANCE-(?:DISCARDED|KEPT)',
                     trial, log, flags=re.S)
    return {'engine': engine,
            'overfull_hbox_pt': [float(v) for v in re.findall(r'Overfull \\hbox \(([\d.]+)pt too wide\)', checked)],
            'overfull_vbox_pt': [float(v) for v in re.findall(vertical, checked)],
            'discarded_balance_vbox_pt': discarded,
            'missing_glyphs': list(dict.fromkeys(re.findall(r'Missing character:.*', log))),
            'underfull_boxes': log.count('Underfull \\hbox')}


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
    report = compile_report(log, Path(engine).name)
    (project / 'compile_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if report['missing_glyphs']:
        raise RuntimeError('Missing font glyphs; PDF is not eligible for promotion.')
    return project / 'main.pdf', report
