import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings as django_settings
from django.core.cache import cache

from apps.submissions.document_analysis import read_file_bytes
from document_template_engine import (
    DocumentTemplateEngineError,
    build_docx_from_template,
    build_docx_plan,
    normalize_template_rules,
)
from paper_formatter.config import SemanticSettings
from paper_formatter.exceptions import PaperFormatterError
from paper_formatter.pipeline import ConversionPipeline
from apps.directory.formatting_templates import has_manual_rule_overrides
from apps.submissions.paper_formatter_ai import QwenSemanticProvider


class FormattingCorrectionError(ValueError):
    pass


_CORRECTED_DOCUMENT_CACHE_REVISION = "paper-formatter-v3"


def _corrected_document_cache_key(
    submission,
    original_bytes,
    rules,
    template_file,
    *,
    metadata=None,
):
    digest = hashlib.sha256()
    digest.update(_CORRECTED_DOCUMENT_CACHE_REVISION.encode("ascii"))
    digest.update(original_bytes)
    digest.update(
        json.dumps(
            rules,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    )
    if template_file is not None:
        template_name, template_bytes = template_file
        digest.update(template_name.encode("utf-8", errors="replace"))
        digest.update(template_bytes)
    if metadata is not None:
        digest.update(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
    submission_pk = getattr(submission, "pk", None)
    if submission_pk is None:
        submission_pk = f"memory-{id(submission)}"
    return f"corrected-docx:{submission_pk}:{digest.hexdigest()}"


def _cached_corrected_document(cache_key):
    try:
        cached = cache.get(cache_key)
    except Exception:
        return None
    if (
        isinstance(cached, tuple)
        and len(cached) == 2
        and isinstance(cached[0], bytes)
        and isinstance(cached[1], list)
    ):
        return cached
    return None


def _store_corrected_document(cache_key, result):
    timeout = getattr(django_settings, "CORRECTED_DOCUMENT_CACHE_TIMEOUT", 3600)
    try:
        cache.set(cache_key, result, timeout=timeout)
    except Exception:
        pass
    return result


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
    document_rules = rules.get("document") or {}
    if document_rules.get("rules_reliable") is False:
        raise FormattingCorrectionError(
            str(document_rules.get("source_notice") or "")
            or (
                "Загруженный PDF похож на готовую статью, поэтому его поля и "
                "разделы нельзя безопасно применять как шаблон."
            )
        )
    with version.file.open("rb") as source:
        original_bytes = read_file_bytes(source)
    return original_bytes, rules


def _template_file(submission):
    if has_manual_rule_overrides(submission.formatting_rules_snapshot):
        return None
    template = submission.formatting_template
    if template is None or not template.file:
        return None
    suffix = Path(template.file.name).suffix.casefold()
    if suffix not in {".docx", ".pdf", ".tex", ".zip"}:
        return None
    with template.file.open("rb") as source:
        return Path(template.file.name).name, read_file_bytes(source)


def _build_with_ported_formatter(original_bytes, template_file):
    template_name, template_bytes = template_file
    with TemporaryDirectory(prefix="webtstu-paper-formatter-") as temporary:
        workdir = Path(temporary)
        source_path = workdir / "source.docx"
        template_path = workdir / template_name
        output_path = workdir / "output"
        source_path.write_bytes(original_bytes)
        template_path.write_bytes(template_bytes)

        semantic_settings = SemanticSettings(enabled=True, provider="qwen")
        result = ConversionPipeline(
            semantic_settings=semantic_settings,
            semantic_provider=QwenSemanticProvider(semantic_settings),
        ).run(
            source_path,
            output_path,
            example=template_path,
            compile_pdf=False,
            render_docx=True,
        )
        if result.docx is None or not result.docx.exists():
            raise FormattingCorrectionError(
                "Перенесённый конвейер не сформировал итоговый DOCX."
            )
        changes = [
            "применён полный профиль файла-шаблона",
            "перенесены реальные стили и структура DOCX-образца",
            "сохранены структурные блоки, формулы, таблицы, рисунки и ссылки",
            "для неоднозначных структурных ролей подключена локальная модель Qwen",
            "сформирован редактируемый DOCX и переносимый LaTeX-проект",
        ]
        changes.extend(
            f"предупреждение конвертера: {warning}"
            for warning in result.run.warnings[:8]
        )
        return result.docx.read_bytes(), changes


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
    template_file = _template_file(submission)
    if template_file is not None:
        cache_key = _corrected_document_cache_key(
            submission,
            original_bytes,
            rules,
            template_file,
        )
        cached = _cached_corrected_document(cache_key)
        if cached is not None:
            return cached
        try:
            result = _build_with_ported_formatter(original_bytes, template_file)
            return _store_corrected_document(cache_key, result)
        except (PaperFormatterError, OSError, ValueError) as exc:
            raise FormattingCorrectionError(
                "Новый редактор не смог применить файл-шаблон: " f"{exc}"
            ) from exc

    # Текстовые требования и подтверждённые ручные правила не содержат DOCX-
    # стилей, поэтому остаются отдельным детерминированным режимом, а не
    # аварийным fallback при ошибке нового редактора.
    metadata = _submission_metadata(submission)
    cache_key = _corrected_document_cache_key(
        submission,
        original_bytes,
        rules,
        None,
        metadata=metadata,
    )
    cached = _cached_corrected_document(cache_key)
    if cached is not None:
        return cached
    try:
        corrected_bytes, changes, _plan = build_docx_from_template(
            original_bytes,
            rules,
            metadata=metadata,
        )
    except DocumentTemplateEngineError as exc:
        raise FormattingCorrectionError(str(exc)) from exc
    return _store_corrected_document(cache_key, (corrected_bytes, changes))
