from pathlib import Path

from apps.submissions.document_analysis import read_file_bytes
from document_template_engine import (
    DocumentTemplateEngineError,
    build_docx_from_template,
    build_docx_plan,
    normalize_template_rules,
)


class FormattingCorrectionError(ValueError):
    pass


def _submission_metadata(submission):
    return {
        "title": submission.title,
        "authors": submission.document_authors or submission.get_authors_display(),
        "organizations": submission.organizations,
        "abstract": submission.abstract,
        "keywords": submission.keywords,
    }


def _source_docx_and_rules(submission):
    version = submission.current_version
    if version is None or not version.file:
        raise FormattingCorrectionError("У заявки нет текущей версии файла.")
    if Path(version.file.name).suffix.casefold() != ".docx":
        raise FormattingCorrectionError("Конструктор документа доступен только для DOCX.")

    rules = normalize_template_rules(
        (submission.formatting_rules_snapshot or {}).get("effective") or {}
    )
    if not rules:
        raise FormattingCorrectionError("Для этой заявки не сохранены правила оформления.")
    with version.file.open("rb") as source:
        original_bytes = read_file_bytes(source)
    return original_bytes, rules


def build_document_template_plan(submission):
    original_bytes, rules = _source_docx_and_rules(submission)
    try:
        return build_docx_plan(
            original_bytes,
            rules,
            metadata=_submission_metadata(submission),
        )
    except DocumentTemplateEngineError as exc:
        raise FormattingCorrectionError(str(exc)) from exc


def build_corrected_docx(submission):
    original_bytes, rules = _source_docx_and_rules(submission)
    try:
        corrected_bytes, changes, _plan = build_docx_from_template(
            original_bytes,
            rules,
            metadata=_submission_metadata(submission),
        )
    except DocumentTemplateEngineError as exc:
        raise FormattingCorrectionError(str(exc)) from exc
    return corrected_bytes, changes
