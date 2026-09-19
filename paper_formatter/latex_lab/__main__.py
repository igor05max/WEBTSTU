"""Run an isolated experiment; never creates or modifies production jobs."""
import argparse
from pathlib import Path
import os
import shutil
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-docx', type=Path)
    parser.add_argument('--baseline-pdf', type=Path)
    parser.add_argument('--qwen', action='store_true')
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--search', action='store_true', help='Compare three bounded typography/layout plans before optional Qwen advice.')
    args = parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from .bridge import NativeBridge
    from .typesetter import default_plan, render, compile_pdf
    from .quality import measure, gate, render_pages, save_json

    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    native = out / 'native'; native.mkdir(exist_ok=True)
    docx, pdf = native / 'result.docx', native / 'result.pdf'
    if not args.reuse or not docx.exists():
        if args.baseline_docx and args.baseline_pdf:
            shutil.copy2(args.baseline_docx, docx); shutil.copy2(args.baseline_pdf, pdf)
        else:
            from apps.template_workspace.v2.inspector.document import DocumentInspector
            from apps.template_workspace.v2.classification.roles import RoleClassifierV2
            from apps.template_workspace.v2.styles.jamt import prepare_jamt_style
            from apps.template_workspace.v2.editor.safe_word_editor import SafeWordEditor
            from apps.template_workspace.v2.exports import export_result_pdf
            report = DocumentInspector(args.source).inspect()
            structure = RoleClassifierV2().article_structure(report)
            template, template_report, profile = prepare_jamt_style(native, article_report=report, article_structure=structure)
            result = SafeWordEditor().render(article_path=args.source, template_path=template,
                output_path=docx, article_report=report, article_structure=structure,
                template_report=template_report, template_profile=profile)
            save_json(native / 'editor_report.json', result.to_dict())
            exported = export_result_pdf(docx, native)
            if exported['status'] != 'completed':
                raise RuntimeError('Native baseline PDF failed.')
    print('Native baseline ready', flush=True)
    project = out / 'latex'
    blocks, manifest = NativeBridge(docx, project).build()
    plan = default_plan(blocks, running_footer=manifest['running_footer'])
    render(blocks, project, plan)
    latex_pdf, compilation = compile_pdf(project)
    native_metrics = measure(pdf, manifest)
    candidate_metrics = measure(latex_pdf, manifest, compilation)
    trials = []
    if args.search:
        from .qwen import penalty
        best_plan, best_metrics, best_project = plan, candidate_metrics, project
        trials.append({'name':'measured_style','metrics':candidate_metrics,
                       'gate':gate(candidate_metrics,native_metrics),'penalty':penalty(candidate_metrics)})
        shutil.copytree(project, out/'latex_initial', dirs_exist_ok=True)
        for name, leading, front_gap, caption_gap, scale in [('balanced',12.4,7.0,4.5,0.98),('compact',12.1,6.0,4.0,0.95)]:
            trial_plan = json.loads(json.dumps(plan))
            trial_plan.update(body_leading=leading,front_gap=front_gap,caption_gap=caption_gap)
            for obj in trial_plan['objects'].values():
                obj['scale'] = scale
            trial = out/('latex_'+name)
            shutil.copytree(project/'assets',trial/'assets',dirs_exist_ok=True)
            render(blocks,trial,trial_plan)
            try:
                trial_pdf, compiled = compile_pdf(trial)
                metrics = measure(trial_pdf,manifest,compiled)
                passed = gate(metrics,native_metrics)
                trials.append({'name':name,'metrics':metrics,'gate':passed,'penalty':penalty(metrics)})
                if passed['passed'] and (not gate(best_metrics,native_metrics)['passed'] or penalty(metrics) < penalty(best_metrics)):
                    best_plan,best_metrics,best_project = trial_plan,metrics,trial
            except RuntimeError as exc:
                trials.append({'name':name,'failed':str(exc)[:200]})
        if best_project != project:
            shutil.copytree(best_project,project,dirs_exist_ok=True)
        plan,candidate_metrics = best_plan,best_metrics
    report = {'native': native_metrics, 'latex': candidate_metrics,
              'latex_gate': gate(candidate_metrics, native_metrics),
              'bounded_search': trials,
              'source': str(args.source), 'automatic_production_promotion': False}
    save_json(out / 'comparison.json', report)
    render_pages(pdf, native / 'pages')
    render_pages(latex_pdf, project / 'pages')
    print('Compiled', {key: report[key]['pages'] for key in ['native', 'latex']}, report['latex_gate'], flush=True)
    if args.qwen:
        from .qwen import optimize
        optimize(blocks, manifest, plan, out, report)


if __name__ == '__main__':
    main()
