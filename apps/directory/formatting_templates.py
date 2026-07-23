import io
import json
import re
from collections import Counter
from pathlib import Path

from django.db import models, transaction

from apps.checks.gemini_client import (
    GeminiAPIError,
    extract_response_text,
    generate_content,
    get_configured_model,
    is_ai_configured,
)
from apps.directory.models import (
    FormattingTemplate,
    FormattingTemplateStatus,
)
from apps.submissions.document_analysis import (
    TEXT_EXTENSIONS,
    analyze_document_bytes,
    read_file_bytes,
)


TEMPLATE_EXTENSIONS = {
    ".docx",
    ".doc",
    ".pdf",
    ".txt",
    ".md",
    ".rtf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}

DEFAULT_RULES = {
    "article": {"limits": {"min_words": 2000, "max_words": 12000}},
    "monograph": {"limits": {"min_words": 10000, "max_words": 200000}},
    "theses": {"limits": {"min_words": 500, "max_words": 5000}},
}

AI_RULE_SCHEMA = {
    "page": {
        "size": "",
        "orientation": "",
        "margins_cm": {"top": None, "right": None, "bottom": None, "left": None},
    },
    "body": {
        "font_family": "",
        "font_size_pt": None,
        "line_spacing": None,
        "first_line_indent_cm": None,
        "alignment": "",
    },
    "structure": {"required_sections": [], "section_order": []},
    "limits": {
        "min_pages": None,
        "max_pages": None,
        "min_words": None,
        "max_words": None,
    },
    "metadata": {"required_fields": []},
    "references": {"style": "", "minimum_count": None},
    "figures": {"captions_required": None},
    "tables": {"captions_required": None},
    "languages": [],
    "filename_rule": "",
    "notes": [],
}


def _round_cm(length):
    if length is None:
        return None
    return round(float(length.cm), 2)


def _round_pt(length):
    if length is None:
        return None
    return round(float(length.pt), 1)


def _dominant(values):
    cleaned = [value for value in values if value not in (None, "")]
    if not cleaned:
        return None
    return Counter(cleaned).most_common(1)[0][0]


def _extract_docx_rules(data):
    try:
        from docx import Document
    except ImportError:
        return {}

    document = Document(io.BytesIO(data))
    page_rules = {}
    if document.sections:
        section = document.sections[0]
        width_cm = _round_cm(section.page_width)
        height_cm = _round_cm(section.page_height)
        page_rules = {
            "size": "A4" if width_cm and height_cm and sorted((round(width_cm), round(height_cm))) == [21, 30] else "",
            "orientation": "landscape" if width_cm and height_cm and width_cm > height_cm else "portrait",
            "margins_cm": {
                "top": _round_cm(section.top_margin),
                "right": _round_cm(section.right_margin),
                "bottom": _round_cm(section.bottom_margin),
                "left": _round_cm(section.left_margin),
            },
        }

    font_names = []
    font_sizes = []
    line_spacings = []
    first_line_indents = []
    alignments = []
    for paragraph in document.paragraphs[:1000]:
        if not paragraph.text.strip():
            continue
        style_name = (paragraph.style.name or "").casefold() if paragraph.style else ""
        if "heading" in style_name or "заголов" in style_name:
            continue
        for run in paragraph.runs:
            if run.text.strip():
                font_names.append(run.font.name)
                font_sizes.append(_round_pt(run.font.size))
        formatting = paragraph.paragraph_format
        spacing = formatting.line_spacing
        if isinstance(spacing, (int, float)):
            line_spacings.append(round(float(spacing), 2))
        first_line_indents.append(_round_cm(formatting.first_line_indent))
        if paragraph.alignment is not None:
            alignments.append(str(paragraph.alignment).split()[0].casefold())

    return {
        "page": page_rules,
        "body": {
            "font_family": _dominant(font_names) or "",
            "font_size_pt": _dominant(font_sizes),
            "line_spacing": _dominant(line_spacings),
            "first_line_indent_cm": _dominant(first_line_indents),
            "alignment": _dominant(alignments) or "",
        },
    }


def _extract_template_content(template):
    with template.file.open("rb") as source:
        data = read_file_bytes(source)
    suffix = Path(template.file.name).suffix.casefold()
    snapshot = analyze_document_bytes(data, template.file.name)
    text = snapshot.get("text") or ""
    deterministic_rules = _extract_docx_rules(data) if suffix == ".docx" else {}

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            text = ""
    elif suffix in TEXT_EXTENSIONS:
        text = snapshot.get("text") or ""
    return text[:120_000], deterministic_rules, snapshot.get("parse_error") or ""


def _parse_json_object(value):
    cleaned = str(value or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _merge_dict(base, override):
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        elif value not in (None, "", [], {}):
            result[key] = value
    return result


def _extract_rules_with_ai(template, text):
    if not text.strip() or not is_ai_configured():
        return {}
    prompt = (
        "Ты извлекаешь только явно указанные или надёжно выводимые правила оформления научного материала. "
        "Не придумывай требований. Верни один JSON-объект без Markdown по указанной схеме. "
        "Если правило не найдено, оставь пустую строку, null или пустой список.\n\n"
        f"Тип материала: {template.article_type.name}\n"
        f"Источник: {template.target_name}\n"
        f"Схема JSON: {json.dumps(AI_RULE_SCHEMA, ensure_ascii=False)}\n\n"
        f"Текст шаблона или требований:\n{text}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }
    response, _model = generate_content(
        payload,
        model=get_configured_model(),
        timeout=120,
    )
    return _parse_json_object(extract_response_text(response))


def build_rules_snapshot(*, article_type, template=None, journal=None):
    effective = _merge_dict({}, DEFAULT_RULES.get(article_type.code, {}))
    sources = [{"kind": "material_type", "label": article_type.name, "priority": 10}]
    conflicts = []

    policy = getattr(journal, "editorial_policy", {}) if journal is not None else {}
    if isinstance(policy, dict) and policy:
        journal_rules = {
            "structure": {"required_sections": policy.get("required_sections") or []},
            "limits": {
                "min_words": policy.get("min_words"),
                "max_words": policy.get("max_words"),
            },
        }
        effective = _merge_dict(effective, journal_rules)
        sources.append({"kind": "journal", "label": journal.name, "priority": 20})

    if template is not None and template.extracted_rules:
        old_limits = dict(effective.get("limits") or {})
        effective = _merge_dict(effective, template.extracted_rules)
        new_limits = effective.get("limits") or {}
        for key in ("min_words", "max_words"):
            if old_limits.get(key) and new_limits.get(key) and old_limits[key] != new_limits[key]:
                conflicts.append(
                    {
                        "field": f"limits.{key}",
                        "lower_value": old_limits[key],
                        "selected_value": new_limits[key],
                        "message": "Требование шаблона имеет приоритет над общим правилом типа материала.",
                    }
                )
        sources.append(
            {
                "kind": "uploaded_template",
                "label": f"{template.target_name}, шаблон v{template.version_number}",
                "priority": 30,
                "template_id": template.id,
            }
        )
    return {"effective": effective, "sources": sources, "conflicts": conflicts}


@transaction.atomic
def create_formatting_template(
    *,
    article_type,
    uploaded_by,
    file,
    journal=None,
    publication_topic=None,
):
    if (journal is None) == (publication_topic is None):
        raise ValueError("Шаблон должен относиться либо к журналу, либо к теме/событию.")
    suffix = Path(file.name or "").suffix.casefold()
    if suffix not in TEMPLATE_EXTENSIONS:
        allowed = ", ".join(sorted(value.lstrip(".").upper() for value in TEMPLATE_EXTENSIONS))
        raise ValueError(f"Формат шаблона не поддерживается. Разрешены: {allowed}.")

    filters = {"article_type": article_type}
    if journal is not None:
        filters["journal"] = journal
    else:
        filters["publication_topic"] = publication_topic
    last_version = (
        FormattingTemplate.objects.select_for_update()
        .filter(**filters)
        .aggregate(value=models.Max("version_number"))["value"]
        or 0
    )
    return FormattingTemplate.objects.create(
        article_type=article_type,
        journal=journal,
        publication_topic=publication_topic,
        version_number=last_version + 1,
        file=file,
        uploaded_by=uploaded_by,
    )


def process_formatting_template(template):
    template.analysis_status = FormattingTemplateStatus.PROCESSING
    template.analysis_message = "Извлекаем правила из шаблона."
    template.save(update_fields=["analysis_status", "analysis_message"])
    try:
        text, deterministic_rules, parse_warning = _extract_template_content(template)
        ai_rules = {}
        ai_warning = ""
        try:
            ai_rules = _extract_rules_with_ai(template, text)
        except (GeminiAPIError, ValueError) as exc:
            ai_warning = str(exc)
        extracted_rules = _merge_dict(ai_rules, deterministic_rules)
        template.source_text = text
        template.extracted_rules = extracted_rules
        template.rule_conflicts = []
        if extracted_rules:
            template.analysis_status = (
                FormattingTemplateStatus.READY
                if text and not parse_warning and not ai_warning
                else FormattingTemplateStatus.PARTIAL
            )
            warnings = [value for value in (parse_warning, ai_warning) if value]
            template.analysis_message = (
                "Правила извлечены."
                if not warnings
                else "Правила извлечены частично. " + " ".join(warnings)
            )
        else:
            template.analysis_status = FormattingTemplateStatus.PARTIAL
            template.analysis_message = (
                "Файл сохранён, но автоматически извлечь правила не удалось. "
                "Правила можно уточнить вручную для конкретной работы."
            )
    except Exception as exc:
        template.analysis_status = FormattingTemplateStatus.FAILED
        template.analysis_message = f"Не удалось обработать шаблон: {type(exc).__name__}."
    template.save(
        update_fields=[
            "analysis_status",
            "analysis_message",
            "source_text",
            "extracted_rules",
            "rule_conflicts",
        ]
    )
    return template


def get_latest_formatting_template(*, article_type, journal=None, publication_topic=None):
    queryset = FormattingTemplate.objects.filter(article_type=article_type)
    if journal is not None:
        queryset = queryset.filter(journal=journal)
    elif publication_topic is not None:
        queryset = queryset.filter(publication_topic=publication_topic)
    else:
        return None
    return queryset.select_related("uploaded_by", "article_type", "journal", "publication_topic").order_by(
        "-version_number",
        "-created_at",
    ).first()
