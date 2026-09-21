"""JAMT PDF branch from the final native DOCX, including editorial marks.

The native DOCX/PDF remain available if a construct cannot be represented safely.
LaTeX sources never contain model-authored article text or executable user TeX.
"""
from hashlib import sha256
from pathlib import Path
import json
import logging
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

from django.conf import settings

logger = logging.getLogger(__name__)


def export_jamt_latex(source, directory, baseline_pdf):
    from paper_formatter.latex_lab.bridge import NativeBridge
    from paper_formatter.latex_lab.typesetter import default_plan, render, compile_pdf
    from paper_formatter.latex_lab.master_template import TEMPLATE_FILES, load_style
    from paper_formatter.latex_lab.quality import measure, gate
    directory, source = Path(directory), Path(source)
    project = directory/'latex'
    destination, bundle = directory/'result-latex.pdf', directory/'result-latex.zip'
    report = {'status': 'unavailable', 'style_version': load_style()['version'], 'source_docx_sha256': sha256(source.read_bytes()).hexdigest(),
              'content_source': 'reviewed_native_docx', 'native_output_changed': False}
    destination.unlink(missing_ok=True); bundle.unlink(missing_ok=True)
    try:
        blocks, manifest = NativeBridge(source, project).build()
        plan = default_plan(blocks, **{key: manifest[key] for key in ('running_footer', 'running_header', 'page_start', 'reference_layout')})
        render(blocks, project, plan)
        report['template'] = plan['template']
        pdf, compilation = compile_pdf(project)
        metrics = measure(pdf, manifest, compilation)
        baseline = measure(baseline_pdf, manifest)
        acceptance = gate(metrics, baseline)
        report.update(metrics=metrics, gate=acceptance, text_transfer=manifest['text_transfer'],
                      equations=len(manifest['formulas']), numbered_items=len(manifest['list_labels']))
        if not acceptance['passed']:
            raise ValueError('LaTeX content/layout gate failed.')
        shutil.copy2(pdf, destination)
        with ZipFile(bundle, 'w', ZIP_DEFLATED) as archive:
            for path in sorted(project.rglob('*')):
                relative = path.relative_to(project)
                if path.is_file() and (relative.parts[0] in {'assets', 'fonts'} or path.name in TEMPLATE_FILES | {'main.tex', 'manifest.json', 'layout_plan.json'}):
                    archive.write(path, relative.as_posix())
            archive.writestr('README.txt', 'Compile twice with XeLaTeX: xelatex -no-shell-escape main.tex\n'
                'Requires TeX Live, Liberation Serif (or Times New Roman) and DejaVu Sans.\n'
                'Editable Word output remains native. Article content and editorial marks come from the final DOCX.\n')
        report.update(status='completed', pages=metrics['pages'], pdf_sha256=sha256(destination.read_bytes()).hexdigest(),
                      message='PDF собран в LaTeX. Проверены перенос текста, нумерация, наличие символов и выход за границы страницы.')
        if getattr(settings, 'TEMPLATE_V2_VISUAL_REVIEW_ENABLED', False):
            from paper_formatter.latex_lab.reference_review import compare_all_pages
            try:
                report['visual_review'] = compare_all_pages(baseline_pdf, destination, directory/'latex-visual-review.json',
                    budget_seconds=600, max_pages=24)
                report['visual_review']['baseline_kind'] = 'formatted_native_docx'
            except Exception as exc:
                report['visual_review'] = {'status': 'unavailable', 'error_type': type(exc).__name__}
    except Exception as exc:
        logger.warning('JAMT LaTeX branch unavailable: %s', type(exc).__name__)
        destination.unlink(missing_ok=True); bundle.unlink(missing_ok=True)
        report.update(status='unavailable', error_type=type(exc).__name__,
                      message='LaTeX-вариант не прошёл проверку или содержит пока неподдерживаемые объекты. Используйте готовые DOCX и PDF Word.')
    (directory/'latex-export-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
