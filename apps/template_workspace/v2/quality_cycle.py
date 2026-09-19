"""Bounded edit → render → check → repair transactions; best valid file wins."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import time

from django.conf import settings
from .readability import run_readability_review
from .editor.repair_actions import structural_issues, select_actions, apply_actions


def _write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def quality_status(review, structural):
    if review['issues'] or structural:
        return 'needs_review'
    if review['status'] == 'reviewed' and review.get('pages_total', 0) and not review.get('warnings'):
        return 'checked'
    if review['status'] == 'partial':
        return 'partially_checked'
    return 'unverified'


def improvement(before, after, structural_before, structural_after):
    """Never reward fewer inspected pages or a failed re-render."""
    if after['status'] not in {'reviewed', 'partial'} or not after.get('pages_checked'):
        return False, 'recheck_unavailable'
    if before.get('pages_total') != after.get('pages_total'):
        # Pagination changes require complete inspection of BOTH versions.
        if before['status'] != 'reviewed' or after['status'] != 'reviewed':
            return False, 'pagination_changed_with_partial_coverage'
    elif not set(before.get('pages_checked', ())).issubset(after['pages_checked']):
        return False, 'coverage_regression'
    if set(before.get('rendered_target_ids', ())) - set(after.get('rendered_target_ids', ())):
        return False, 'rendered_content_regression'
    def score(items):
        return sum({'high':9, 'medium':3, 'low':1}.get(x['severity'], 1) for x in items)
    def hard(review):
        return [x for x in review['issues'] if x.get('source') == 'pdf-geometry']
    if len(hard(after)) > len(hard(before)) or len(structural_after) > len(structural_before):
        return False, 'structural_regression'
    if sum(x['severity'] == 'high' for x in after['issues']) > sum(x['severity'] == 'high' for x in before['issues']):
        return False, 'severe_visual_regression'
    old_severe = {(x['kind'], x.get('target_id') or x['page']) for x in before['issues'] if x['severity'] == 'high'}
    new_severe = {(x['kind'], x.get('target_id') or x['page']) for x in after['issues'] if x['severity'] == 'high'}
    if new_severe - old_severe:
        return False, 'new_severe_defect'
    if score(after['issues']) + score(structural_after) >= score(before['issues']) + score(structural_before):
        return False, 'no_measured_improvement'
    return True, 'improved_without_detected_regression'


def repairs_covered(review, actions):
    """A sampled review cannot authorize edits on unseen pages or neighbours."""
    if review['status'] == 'reviewed':
        return True
    checked = set(review.get('pages_checked', ()))
    total = review.get('pages_total', 0)
    locations = {t['id']:int(page) for page, targets in review.get('grounded_targets', {}).items() for t in targets}
    for action in actions:
        page = locations.get(action['target_id'])
        if page is None or not set(range(max(1,page-1), min(total,page+1)+1)).issubset(checked):
            return False
    return True


def run_quality_cycle(result_path, output_directory, *, editor_result, template_path=None,
                      template_title='', reviewer=None, progress=None, style_id=''):
    reviewer = reviewer or run_readability_review
    result_path, output = Path(result_path), Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    enabled = getattr(settings, 'TEMPLATE_V2_EDIT_CYCLE_ENABLED', True)
    max_passes = max(1, min(3, getattr(settings, 'TEMPLATE_V2_EDIT_CYCLE_MAX_PASSES', 3))) if enabled else 1
    budget = max(1, getattr(settings, 'TEMPLATE_V2_EDIT_CYCLE_BUDGET', 2100))
    pass_budget = getattr(settings, 'TEMPLATE_V2_VISUAL_REVIEW_BUDGET', 900)
    targets = editor_result.metrics.get('layout_targets', [])
    report = dict(status='unverified', max_passes=max_passes, budget_seconds=budget,
                  iterations=[], accepted_repairs=0, rejected_candidates=0, warnings=[],
                  automatic_content_changes=False, best_iteration=0)
    history = output/'quality_cycle'; history.mkdir(exist_ok=True)
    baseline = history/'pass-0'; baseline.mkdir(exist_ok=True)
    best = baseline/'candidate.docx'; shutil.copy2(result_path, best)
    attempted = set()

    def review(path, folder, preferred=()):
        if progress:
            progress('Проверяем внешний вид и читаемость: '+folder.name)
        remaining = max(1, int(budget-(time.monotonic()-started)))
        return reviewer(path, folder, template_path=template_path, template_title=template_title,
                        targets=targets, budget_seconds=min(pass_budget, remaining), preferred_pages=preferred,
                        **({'style_id': style_id} if style_id else {}))

    best_review = {'status':'unavailable', 'pages_checked':[], 'issues':[], 'warnings':[]}
    best_structural = []
    best_folder = baseline
    try:
        best_structural = structural_issues(best, targets)
        best_review = review(best, baseline)
        report['iterations'].append(dict(iteration=0, sha256=_hash(best), accepted=True,
            structural_issues=best_structural, review=best_review, actions=[]))
        for iteration in range(1, max_passes):
            # Without a real visual baseline no candidate can establish improvement.
            if best_review['status'] not in {'reviewed', 'partial'}:
                report['stop_reason'] = 'visual_baseline_unavailable'; break
            if budget-(time.monotonic()-started) < 30:
                report['stop_reason'] = 'cycle_budget_exhausted'; break
            actions = select_actions(best_structural + best_review['issues'], targets, attempted)
            actions = [action for action in actions if repairs_covered(best_review, [action])]
            if not actions:
                report['stop_reason'] = 'no_safe_grounded_actions'; break
            attempted.update((a['target_id'], a['action']) for a in actions)
            folder = history/f'pass-{iteration}'; folder.mkdir(exist_ok=True)
            candidate = folder/'candidate.docx'
            event = dict(iteration=iteration, actions=actions, accepted=False)
            report['iterations'].append(event)
            try:
                if progress:
                    progress(f'Безопасные правки и повторная проверка: проход {iteration + 1}/{max_passes}')
                if not apply_actions(best, candidate, targets, actions, _hash(best)):
                    event['reason'] = 'no_xml_change'; continue
                structural = structural_issues(candidate, targets)
                current = review(candidate, folder, best_review.get('pages_checked', ()))
                accepted, reason = improvement(best_review, current, best_structural, structural)
                if accepted and not repairs_covered(current, actions):
                    accepted, reason = False, 'target_neighbourhood_not_rechecked'
                event.update(accepted=accepted, reason=reason, structural_issues=structural, review=current,
                             sha256=_hash(candidate), native_content_preserved=True)
                if accepted:
                    best, best_review, best_structural, best_folder = candidate, current, structural, folder
                    report['accepted_repairs'] += len(actions)
                    report['best_iteration'] = iteration
                else:
                    report['rejected_candidates'] += 1
                    # Repeated stochastic guessing is not a repair strategy.
                    report['stop_reason'] = reason; break
            except Exception as exc:
                event['reason'] = 'candidate_failed:' + type(exc).__name__
                report['rejected_candidates'] += 1
                report['warnings'].append('Пробная правка отменена; сохранён предыдущий документ ('+type(exc).__name__+').')
                report['stop_reason'] = 'candidate_failed'; break
        else:
            report['stop_reason'] = 'pass_limit'
    except Exception as exc:
        report['warnings'].append('Цикл проверки прерван; готовый DOCX сохранён ('+type(exc).__name__+').')
        report['stop_reason'] = 'review_failed'
    # Promotion is atomic and happens only after the full transaction succeeded.
    if report['best_iteration']:
        promote = output/'quality-promote.docx'
        shutil.copy2(best, promote); promote.replace(result_path)
    for name in ('readability-preview.pdf', 'readability-template.pdf'):
        if (best_folder/name).is_file():
            shutil.copy2(best_folder/name, output/name)
    report.update(status=quality_status(best_review, best_structural),
                  final_sha256=_hash(result_path), remaining_structural_issues=best_structural,
                  elapsed_seconds=round(time.monotonic()-started, 2))
    best_review['quality_status'] = report['status']
    best_review['warnings'].extend(report['warnings'])
    _write(output/'quality_cycle_report.json', report)
    _write(output/'readability_report.json', best_review)
    editor_result.metrics['quality_cycle'] = {key:report[key] for key in
        ('status', 'accepted_repairs', 'rejected_candidates', 'best_iteration', 'final_sha256')}
    _write(output/'editor_report.json', editor_result.to_dict())
    return best_review, report


def cycle_summary(report):
    labels = {'checked':'проверенные страницы без замечаний', 'needs_review':'остались замечания — требуется проверка',
              'partially_checked':'проверена только часть страниц', 'unverified':'качество не подтверждено'}
    return (f"Файл собран. Качество: {labels[report['status']]}. Проходов: {len(report['iterations'])}; "
            f"принято правок: {report['accepted_repairs']}; отменено вариантов: {report['rejected_candidates']}.")
