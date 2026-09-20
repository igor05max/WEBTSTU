"""Independent PDF measurements. These are gates, not a claim of peer review."""
from collections import Counter
import json
from pathlib import Path
import re
import unicodedata

import pymupdf as fitz


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold().replace('\u00ad', '')
    return ''.join(c for c in text if c.isalnum())


def words(text):
    text = unicodedata.normalize('NFKC', text).casefold().replace('\u00ad', '')
    text = re.sub(r'(?<=\w)[\-‐‑]\s*(?=\w)', '', text)
    return Counter(re.findall(r'[^\W_]+', text))


def measure(pdf_path, manifest, compile_report=None):
    pages, texts, body_texts, out_of_page = [], [], [], []
    with fitz.open(pdf_path) as pdf:
        for i, page in enumerate(pdf):
            text = page.get_text(sort=False)
            texts.append(text)
            rect = page.rect
            body_texts.append(page.get_text('text', clip=fitz.Rect(0, 74, rect.width, rect.height-58)))
            spans = [s for b in page.get_text('dict')['blocks'] if b['type'] == 0 for l in b['lines'] for s in l['spans']]
            # Headers/footers are excluded from body whitespace statistics.
            body_spans = [s for s in spans if s['bbox'][1] >= 74 and s['bbox'][3] <= rect.height - 58]
            images = page.get_image_info()
            bounds = [s['bbox'] for s in body_spans] + [x['bbox'] for x in images]
            gaps = []
            for left, right in [(51, rect.width/2-8), (rect.width/2+8, rect.width-51)]:
                intervals = sorted((max(85, b[1]), min(rect.height-68, b[3])) for b in bounds
                                   if b[2] > left and b[0] < right and b[3] >= 85 and b[1] <= rect.height-68)
                bottom, col_gaps = 85, []
                for top, end in intervals:
                    if top - bottom > 42:
                        col_gaps.append(round(top-bottom, 2))
                    bottom = max(bottom, end)
                gaps.extend(col_gaps)
            for word in page.get_text('words'):
                if word[0] < -1 or word[1] < -1 or word[2] > rect.width+1 or word[3] > rect.height+1:
                    out_of_page.append({'page': i+1, 'text': word[4]})
            pages.append({'page': i+1, 'images': len(images), 'large_internal_gaps_pt': gaps,
                          'body_last_y': round(max((b[3] for b in bounds), default=85), 2),
                          'body_characters': sum(len(s['text']) for s in body_spans)})
    text = '\n'.join(texts)
    source_words, output_words = words(manifest['source_text']), words(text)
    missing_words = source_words - output_words
    normalized = normalize(text)
    # A paragraph may cross a page; running furniture is not inserted content.
    body_normalized = normalize('\n'.join(body_texts))
    missing_labels = [label for label in manifest.get('list_labels', [])
                      if normalize(label['label'] + label['text_anchor'][:60]) not in body_normalized]
    missing = []
    blocks = [b for b in manifest['blocks']]
    blocks += [c for b in manifest['blocks'] for c in b['captions']]
    for b in blocks:
        # Tables interleave cells in extraction; count their text tokens separately.
        wanted = normalize(b['text'])
        if len(wanted) >= 30 and b['kind'] != 'table' and wanted not in normalized:
            missing.append({'id': b['id'], 'role': b['role'], 'preview': b['text'][:120]})
    coverage = 1 - sum(missing_words.values()) / max(1, sum(source_words.values()))
    result = {'pages': len(pages), 'word_coverage': round(coverage, 6),
              'missing_words': dict(missing_words.most_common(40)),
              'unmatched_text_blocks': missing, 'page_metrics': pages,
              'missing_list_labels': missing_labels,
              'out_of_page': out_of_page, 'compile': compile_report or {},
              'sparse_pages': [p['page'] for p in pages if p['body_characters'] < 200 and p['images'] == 0],
              'limitations': ['Word counts and text matching do not establish mathematical equivalence.',
                             'Whitespace is a diagnostic, not a universal aesthetic score.',
                             'All pages and native formulas require visual review.']}
    return result


def gate(candidate, baseline):
    errors = []
    if candidate['word_coverage'] < max(0.99, baseline['word_coverage'] - 0.001):
        errors.append('text_coverage_regression')
    if candidate['out_of_page']:
        errors.append('text_out_of_page')
    if candidate.get('missing_list_labels'):
        errors.append('missing_list_labels')
    if candidate.get('sparse_pages'):
        errors.append('almost_empty_pages')
    compile_report = candidate['compile']
    if compile_report.get('missing_glyphs'):
        errors.append('missing_glyphs')
    if max(compile_report.get('overfull_hbox_pt', [0]) or [0]) > 2:
        errors.append('overfull_horizontal_boxes')
    if max(compile_report.get('overfull_vbox_pt', [0]) or [0]) > 2:
        errors.append('overfull_vertical_boxes')
    return {'passed': not errors, 'errors': errors, 'requires_visual_review': True}


def render_pages(pdf_path, folder, dpi=105):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob('page-*.png'):
        if old.is_file() and old.resolve().parent == folder.resolve():
            old.unlink()
    with fitz.open(pdf_path) as pdf:
        for i, page in enumerate(pdf):
            page.get_pixmap(dpi=dpi).save(folder / f'page-{i+1:02d}.png')


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
