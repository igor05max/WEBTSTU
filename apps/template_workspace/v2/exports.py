"""PDF delivery is independent of optional AI review and uses the final DOCX."""
from hashlib import sha256
import json
import logging
from pathlib import Path
import shutil

import pymupdf

from apps.submissions.document_preview import convert_word_path_to_pdf

logger = logging.getLogger(__name__)


def export_result_pdf(docx_path, directory):
    docx_path, directory = Path(docx_path), Path(directory)
    destination = directory / "result.pdf"
    report = {"status": "unavailable", "engine": "word", "source_docx_sha256": sha256(docx_path.read_bytes()).hexdigest()}
    try:
        convert_word_path_to_pdf(docx_path, destination)
        payload = destination.read_bytes()
        with pymupdf.open(stream=payload, filetype="pdf") as pdf:
            if not len(pdf) or pdf.is_encrypted:
                raise ValueError("Invalid exported PDF")
            report.update(status="completed", pages=len(pdf), pdf_sha256=sha256(payload).hexdigest())
    except Exception as exc:
        logger.exception("Final PDF export failed")
        destination.unlink(missing_ok=True)
        report.update(error_type=type(exc).__name__, message="DOCX готов, но экспорт PDF не завершён. Повторите оформление или обратитесь к администратору.")
    (directory / "export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def select_jamt_pdf(directory, native_report, latex_report):
    """Publish the validated LaTeX PDF; retain Word's exact rendering too."""
    directory=Path(directory)
    report=dict(native_report)
    report.setdefault('engine','word')
    severe=[]
    for event in (latex_report or {}).get('visual_review',{}).get('events',[]):
        letter=next((k for k,v in event.get('mapping',{}).items() if v=='candidate'),None)
        severe.extend(i for i in event.get('response',{}).get('issues_'+str(letter),[])
                      if i.get('severity')=='high' and i.get('category') in {'overlap','clipping','illegible','table_layout'})
    if (latex_report and latex_report.get('status')=='completed'
            and latex_report.get('gate',{}).get('passed') and not severe):
        candidate=directory/'result-latex.pdf'
        # Check the exact artifact which passed the content/layout checks.
        if candidate.exists() and sha256(candidate.read_bytes()).hexdigest()==latex_report.get('pdf_sha256'):
            shutil.copy2(directory/'result.pdf',directory/'result-word.pdf')
            shutil.copy2(candidate,directory/'result.pdf')
            report.update(engine='xelatex',pages=latex_report['pages'],pdf_sha256=latex_report['pdf_sha256'],
                          word_pdf_sha256=native_report.get('pdf_sha256'),selection='passed_content_and_layout_gate')
    if report['engine']=='word':
        report['selection']='visual_review_flagged_latex' if severe else 'latex_unavailable_or_not_validated'
    (directory/'export_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
