"""PDF delivery is independent of optional AI review and uses the final DOCX."""
from hashlib import sha256
import json
import logging
from pathlib import Path

import pymupdf

from apps.submissions.document_preview import convert_word_path_to_pdf

logger = logging.getLogger(__name__)


def export_result_pdf(docx_path, directory):
    docx_path, directory = Path(docx_path), Path(directory)
    destination = directory / "result.pdf"
    report = {"status": "unavailable", "source_docx_sha256": sha256(docx_path.read_bytes()).hexdigest()}
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
