"""Full-page Qwen comparison against an explicitly supplied reference PDF.

No edits or promotion occur here. Report coverage and raw, fallible observations
separately from mechanical content checks and human inspection.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json
import time
from hashlib import sha256

import pymupdf

from .qwen import request, page_image, COMPARE_PROMPT
from .quality import words


def align_pages(reference, candidate):
    with pymupdf.open(reference) as ref, pymupdf.open(candidate) as result:
        refs = [words(p.get_text()) for p in ref]
        matches = []
        for i, page in enumerate(result):
            tokens = words(page.get_text())
            scores = [2*sum((tokens & other).values())/max(1, sum(tokens.values())+sum(other.values())) for other in refs]
            best = max(range(len(scores)), key=scores.__getitem__)
            matches.append({'candidate_page': i+1, 'reference_page': best+1, 'alignment_score': round(scores[best], 4)})
        return matches, len(ref), len(result)


def compare_all_pages(reference, candidate, output, *, budget_seconds=900, max_pages=40):
    reference, candidate, output = Path(reference), Path(candidate), Path(output)
    matches, reference_total, candidate_total = align_pages(reference, candidate)
    deadline = time.monotonic() + budget_seconds
    report = {'status': 'running', 'reference_pages': reference_total, 'candidate_pages': candidate_total,
              'events': [], 'automatic_promotion': False, 'mathematical_accuracy_established_by_model': False,
              'manual_review_required': True,
              'reference_pdf_sha256': sha256(reference.read_bytes()).hexdigest(),
              'candidate_pdf_sha256': sha256(candidate.read_bytes()).hexdigest()}
    def compare(match):
        remaining = deadline - time.monotonic()
        if remaining < 10:
            return {**match, 'status': 'unavailable', 'error_type': 'ReviewBudgetExhausted'}
        index = match['candidate_page']
        order = ['reference', 'candidate'] if index % 2 else ['candidate', 'reference']
        paths = {'reference': reference, 'candidate': candidate}
        pictures = [(letter, page_image(paths[name], match[name+'_page'])) for letter, name in zip('AB', order)]
        try:
            value, model = request(COMPARE_PROMPT + '\nA short final page after author information/license is normal. '
                'Look for actual overlap, broken figures/captions, wrong journal hierarchy or poorly arranged cells. '
                'Fewer pages, tighter text or extra hyphenation are not inherently better.',
                'Compare visual composition. Pagination may differ; do not infer missing scientific content.', pictures, tokens=1500, timeout=min(180, remaining))
            if value.get('preference') not in {'A', 'B', 'tie'} or not isinstance(value.get('reason'), str):
                raise ValueError('Invalid reference review response.')
            for letter in 'AB':
                issues = value.get('issues_'+letter)
                if not isinstance(issues, list) or any(not isinstance(x, dict) or x.get('severity') not in {'low','medium','high'} or not isinstance(x.get('description'), str) for x in issues):
                    raise ValueError('Invalid reference issue response.')
            return {**match, 'status': 'reviewed', 'model': model, 'mapping': dict(zip('AB', order)),
                    'preferred': 'tie' if value['preference'] == 'tie' else order['AB'.index(value['preference'])], 'response': value}
        except Exception as exc:
            return {**match, 'status': 'unavailable', 'error_type': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compare, match) for match in matches[:max_pages]]
        for future in as_completed(futures):
            event = future.result(); report['events'].append(event)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print('Reference review', candidate.parent.parent.name, event['candidate_page'], event['status'], event.get('preferred'), flush=True)
    report['events'].sort(key=lambda e:e['candidate_page'])
    checked = [e for e in report['events'] if e['status'] == 'reviewed']
    report['candidate_pages_checked'] = [e['candidate_page'] for e in checked]
    report['reference_pages_checked'] = sorted({e['reference_page'] for e in checked})
    report['status'] = 'reviewed' if len(checked) == candidate_total and len(report['reference_pages_checked']) == reference_total else 'partial' if checked else 'unavailable'
    report['preferences'] = dict(Counter(e['preferred'] for e in checked))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
