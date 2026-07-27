import io
from collections import Counter
from pathlib import Path
import re
from statistics import median
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from django.db import models, transaction

from apps.checks.gemini_client import (
    GeminiAPIError,
    extract_response_text,
    generate_content,
    get_configured_model,
    get_provider,
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
from document_template_engine import (
    extract_latex_template_rules,
    interpret_template_text,
    normalize_template_rules,
)


TEMPLATE_EXTENSIONS = {
    ".docx",
    ".dotx",
    ".doc",
    ".pdf",
    ".tex",
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


_NON_BODY_STYLE_TOKENS = (
    "abstract",
    "affiliation",
    "article_type",
    "author",
    "back_matter",
    "bullet",
    "caption",
    "heading",
    "itemize",
    "keyword",
    "list",
    "reference",
    "title",
    "аннотац",
    "автор",
    "заголов",
    "ключев",
    "литератур",
    "назван",
    "подпис",
    "список",
)


def _style_chain(style):
    while style is not None:
        yield style
        style = style.base_style


def _first_style_value(style, getter):
    for candidate in _style_chain(style):
        value = getter(candidate)
        if value is not None:
            return value
    return None


def _style_font_size(style):
    try:
        from docx.oxml.ns import qn
    except ImportError:
        return None
    for candidate in _style_chain(style):
        direct_size = _round_pt(candidate.font.size)
        if direct_size is not None:
            return direct_size
        run_properties = candidate.element.find(qn("w:rPr"))
        if run_properties is None:
            continue
        for size_name in ("w:sz", "w:szCs"):
            size = run_properties.find(qn(size_name))
            if size is None:
                continue
            try:
                return round(float(size.get(qn("w:val"))) / 2, 1)
            except (TypeError, ValueError):
                continue
    return None


def _document_default_font_size(document):
    try:
        from docx.oxml.ns import qn
    except ImportError:
        return None
    defaults = document.styles.element.find(qn("w:docDefaults"))
    if defaults is None:
        return None
    run_defaults = defaults.find(qn("w:rPrDefault"))
    run_properties = run_defaults.find(qn("w:rPr")) if run_defaults is not None else None
    if run_properties is None:
        return None
    for size_name in ("w:sz", "w:szCs"):
        size = run_properties.find(qn(size_name))
        if size is None:
            continue
        try:
            return round(float(size.get(qn("w:val"))) / 2, 1)
        except (TypeError, ValueError):
            continue
    return None


def _resolved_style_rules(document, paragraphs):
    if not paragraphs:
        return {}
    normal_style = document.styles["Normal"]
    representative_style = paragraphs[0].style or normal_style
    style_font_size = _style_font_size(representative_style)

    font_names = []
    font_sizes = []
    line_spacings = []
    first_line_indents = []
    alignments = []
    for paragraph in paragraphs:
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            if run.font.name:
                font_names.append(run.font.name)
            if run.font.size is not None:
                font_sizes.append(_round_pt(run.font.size))

        formatting = paragraph.paragraph_format
        spacing = formatting.line_spacing
        if spacing is None:
            spacing = _first_style_value(
                paragraph.style or representative_style,
                lambda style: style.paragraph_format.line_spacing,
            )
        if spacing is None:
            spacing = normal_style.paragraph_format.line_spacing
        indent = formatting.first_line_indent
        if indent is None:
            indent = _first_style_value(
                paragraph.style or representative_style,
                lambda style: style.paragraph_format.first_line_indent,
            )
        if indent is None:
            indent = normal_style.paragraph_format.first_line_indent
        alignment = paragraph.alignment
        if alignment is None:
            alignment = _first_style_value(
                paragraph.style or representative_style,
                lambda style: style.paragraph_format.alignment,
            )
        if alignment is None:
            alignment = normal_style.paragraph_format.alignment

        first_line_indents.append(_round_cm(indent))
        if alignment is not None:
            alignments.append(str(alignment).split()[0].casefold())

        reference_size = (
            _style_font_size(paragraph.style or representative_style)
            or _dominant(font_sizes)
            or _style_font_size(normal_style)
            or _document_default_font_size(document)
        )
        if hasattr(spacing, "pt") and reference_size:
            line_spacings.append(round(float(spacing.pt) / reference_size, 2))
        elif isinstance(spacing, (int, float)):
            line_spacings.append(round(float(spacing), 2))

    style_font_name = _first_style_value(
        representative_style,
        lambda style: style.font.name,
    )
    return {
        "font_family": (
            _dominant(font_names)
            or style_font_name
            or normal_style.font.name
            or ""
        ),
        "font_size_pt": (
            style_font_size
            or _dominant(font_sizes)
            or _style_font_size(normal_style)
            or _document_default_font_size(document)
        ),
        "line_spacing": _dominant(line_spacings),
        "first_line_indent_cm": _dominant(first_line_indents),
        "alignment": _dominant(alignments) or "",
    }


def _body_paragraphs(document):
    candidates = []
    for paragraph in document.paragraphs[:1000]:
        if not paragraph.text.strip():
            continue
        style_name = (paragraph.style.name or "").casefold() if paragraph.style else ""
        if any(token in style_name for token in _NON_BODY_STYLE_TOKENS):
            continue
        candidates.append(paragraph)
    if not candidates:
        candidates = [
            paragraph
            for paragraph in document.paragraphs[:1000]
            if paragraph.text.strip()
        ]
    style_id = _dominant(
        paragraph.style.style_id
        for paragraph in candidates
        if paragraph.style is not None
    )
    selected = [
        paragraph
        for paragraph in candidates
        if paragraph.style is not None and paragraph.style.style_id == style_id
    ]
    return selected or candidates


def _pdf_font_family(base_font):
    name = str(base_font or "").split("+")[-1].casefold()
    if "palladio" in name or "palatino" in name:
        return "Palatino Linotype"
    if "times" in name or "nimbusroman" in name:
        return "Times New Roman"
    if "helvetica" in name or "arial" in name:
        return "Arial"
    if "computer-modern" in name or "latinmodern" in name:
        return "Latin Modern Roman"
    return ""


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _extract_pdf_rules(data):
    """Extract visual page and body rules which plain PDF text cannot retain."""
    try:
        from pypdf import PdfReader
        from pypdf._text_extraction import mult
    except ImportError:
        return {}

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return {}
    if not reader.pages:
        return {}

    first_page = reader.pages[0]
    width_pt = float(first_page.mediabox.width)
    height_pt = float(first_page.mediabox.height)
    fragments = []
    # The title page is often deliberately different. Later pages provide a
    # much more reliable sample of the article's main typography.
    page_indexes = range(1, min(len(reader.pages), 16)) if len(reader.pages) > 1 else range(1)
    for page_index in page_indexes:
        page = reader.pages[page_index]

        def collect(text, cm, tm, font_dict, font_size):
            clean = " ".join(str(text or "").split())
            if not clean or font_dict is None or not (7 <= float(font_size or 0) <= 13):
                return
            x, y = mult(tm, cm)[4:]
            if x <= 0 or y <= 35 or y >= height_pt - 25:
                return
            if re.fullmatch(r"\d+", clean) and float(font_size) < 7:
                return
            fragments.append(
                {
                    "page": page_index,
                    "text": clean,
                    "x": float(x),
                    "y": float(y),
                    "size": round(float(font_size), 2),
                    "base_font": str(font_dict.get("/BaseFont") or ""),
                }
            )

        try:
            page.extract_text(visitor_text=collect)
        except Exception:
            continue

    page_width_cm = width_pt / 72 * 2.54
    page_height_cm = height_pt / 72 * 2.54
    is_a4 = (
        abs(min(page_width_cm, page_height_cm) - 21.0) <= 0.35
        and abs(max(page_width_cm, page_height_cm) - 29.7) <= 0.35
    )
    page_rules = {
        "size": "A4" if is_a4 else "",
        "orientation": "landscape" if width_pt > height_pt else "portrait",
    }
    if not fragments:
        return {"page": page_rules}

    font_weights = Counter()
    for item in fragments:
        if 8 <= item["size"] <= 12:
            font_weights[(item["base_font"], item["size"])] += len(item["text"])
    if not font_weights:
        return {"page": page_rules}
    (body_base_font, body_size), _weight = font_weights.most_common(1)[0]
    body_fragments = [
        item
        for item in fragments
        if item["base_font"] == body_base_font and abs(item["size"] - body_size) <= 0.15
    ]

    x_counts = Counter(round(item["x"]) for item in body_fragments)
    body_left_pt = float(x_counts.most_common(1)[0][0])
    indent_candidates = Counter(
        round(item["x"]) - round(body_left_pt)
        for item in body_fragments
        if 10 <= round(item["x"]) - round(body_left_pt) <= 40
    )
    first_line_indent_pt = (
        float(indent_candidates.most_common(1)[0][0])
        if indent_candidates
        else 0.0
    )

    baselines = {}
    for item in body_fragments:
        baselines.setdefault(item["page"], set()).add(round(item["y"], 1))
    line_gaps = []
    for page_values in baselines.values():
        ordered = sorted(page_values, reverse=True)
        for first, second in zip(ordered, ordered[1:]):
            gap = first - second
            if body_size * 1.05 <= gap <= body_size * 1.8:
                line_gaps.append(gap)
    baseline_gap = median(line_gaps) if line_gaps else body_size
    line_spacing = round(baseline_gap / body_size, 2) if body_size else None

    page_tops = []
    page_bottoms = []
    for page_index in set(item["page"] for item in body_fragments):
        ys = [item["y"] for item in body_fragments if item["page"] == page_index]
        if ys:
            page_tops.append(max(ys))
            page_bottoms.append(min(ys))
    top_baseline = _percentile(page_tops, 0.75)
    bottom_baseline = _percentile(page_bottoms, 0.25)
    margins = {
        "left": round(body_left_pt / 72 * 2.54, 2),
        # PDF text coordinates do not expose a dependable right edge for every
        # embedded font. A narrow journal margin is inferred from the available
        # line width after the dominant left boundary.
        "right": round(max(0.8, min(2.5, (width_pt - body_left_pt) / 72 * 2.54 * 0.08)), 2),
        "top": round((height_pt - top_baseline + body_size * 0.2) / 72 * 2.54, 2),
        "bottom": round(max(1.5, bottom_baseline / 72 * 2.54), 2),
    }
    page_rules["margins_cm"] = margins
    bold_sizes = Counter()
    for item in fragments:
        if "bold" in item["base_font"].casefold() and item["size"] >= body_size:
            bold_sizes[item["size"]] += len(item["text"])
    title_size = max(
        max(bold_sizes, default=0),
        round(body_size * 1.8, 1),
    )

    return {
        "page": page_rules,
        "body": {
            "font_family": _pdf_font_family(body_base_font),
            "font_size_pt": round(body_size, 1),
            "line_spacing": line_spacing,
            "first_line_indent_cm": round(first_line_indent_pt / 72 * 2.54, 2),
            "alignment": "justify",
        },
        "headings": {
            "font_family": _pdf_font_family(body_base_font),
            "font_size_pt": round(body_size, 1),
            "title_font_size_pt": round(title_size, 1),
            "color_hex": "000000",
        },
    }


def _extract_docx_rules(data):
    try:
        from docx import Document
    except ImportError:
        return {}

    source = io.BytesIO(data)
    try:
        document = Document(source)
    except ValueError:
        source = io.BytesIO()
        try:
            with ZipFile(io.BytesIO(data)) as input_archive, ZipFile(
                source,
                "w",
                ZIP_DEFLATED,
            ) as output_archive:
                for info in input_archive.infolist():
                    value = input_archive.read(info)
                    if info.filename == "[Content_Types].xml":
                        value = value.replace(
                            b"application/vnd.openxmlformats-officedocument."
                            b"wordprocessingml.template.main+xml",
                            b"application/vnd.openxmlformats-officedocument."
                            b"wordprocessingml.document.main+xml",
                        )
                    output_archive.writestr(info, value)
        except (OSError, BadZipFile, KeyError):
            return {}
        source.seek(0)
        try:
            document = Document(source)
        except (OSError, ValueError):
            return {}
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

    body_rules = _resolved_style_rules(document, _body_paragraphs(document))
    heading_paragraphs = [
        paragraph
        for paragraph in document.paragraphs[:1000]
        if paragraph.text.strip()
        and paragraph.style is not None
        and (
            "heading" in (paragraph.style.name or "").casefold()
            or "заголов" in (paragraph.style.name or "").casefold()
        )
    ]
    heading_rules = _resolved_style_rules(document, heading_paragraphs)
    title_paragraphs = [
        paragraph
        for paragraph in document.paragraphs[:100]
        if paragraph.text.strip()
        and paragraph.style is not None
        and (
            "title" in (paragraph.style.name or "").casefold()
            or "назван" in (paragraph.style.name or "").casefold()
        )
    ]
    title_rules = _resolved_style_rules(document, title_paragraphs)
    if title_rules.get("font_size_pt"):
        heading_rules["title_font_size_pt"] = title_rules["font_size_pt"]

    result = {
        "page": page_rules,
        "body": body_rules,
    }
    if heading_paragraphs:
        result["headings"] = {
            key: value
            for key, value in heading_rules.items()
            if key
            in {
                "font_family",
                "font_size_pt",
                "title_font_size_pt",
            }
            and value not in (None, "")
        }
    return result


def _extract_template_content(template):
    with template.file.open("rb") as source:
        data = read_file_bytes(source)
    suffix = Path(template.file.name).suffix.casefold()
    snapshot = analyze_document_bytes(data, template.file.name)
    text = snapshot.get("text") or ""
    parse_warning = snapshot.get("parse_error") or ""
    if suffix in {".docx", ".dotx"}:
        deterministic_rules = _extract_docx_rules(data)
    elif suffix == ".pdf":
        deterministic_rules = _extract_pdf_rules(data)
    elif suffix == ".tex":
        deterministic_rules = extract_latex_template_rules(data)
    else:
        deterministic_rules = {}

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if text.strip():
                parse_warning = ""
        except Exception:
            text = ""
    elif suffix == ".tex":
        for encoding in ("utf-8-sig", "utf-8", "cp1251", "utf-16le"):
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            else:
                break
        else:
            text = data.decode("utf-8", errors="replace")
        parse_warning = ""
    elif suffix in TEXT_EXTENSIONS:
        text = snapshot.get("text") or ""
    return text[:120_000], deterministic_rules, parse_warning


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
    if get_provider() != "openai_compatible":
        raise ValueError(
            "Для интерпретации шаблонов требуется локальная OpenAI-совместимая модель."
        )

    def complete_json(prompt):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 6144,
                "responseMimeType": "application/json",
            },
        }
        response, _model = generate_content(
            payload,
            model=get_configured_model(),
            timeout=120,
        )
        return extract_response_text(response)

    return interpret_template_text(
        document_type=template.article_type.name,
        target_name=template.target_name,
        text=text,
        complete_json=complete_json,
    )


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
    return {
        "effective": normalize_template_rules(effective),
        "sources": sources,
        "conflicts": conflicts,
    }


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
        raw_rules = _merge_dict(ai_rules, deterministic_rules)
        extracted_rules = normalize_template_rules(raw_rules) if raw_rules else {}
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
