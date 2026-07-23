import io
import re
from collections import Counter
from pathlib import Path

from apps.submissions.document_analysis import (
    analyze_document_bytes,
    normalize_for_match,
    read_file_bytes,
)


WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’-]*")


def _issue(code, title, message, *, severity="warning", suggestion="", fixable=False):
    return {
        "code": code,
        "title": title,
        "severity": severity,
        "message": message,
        "location": "Оформление по шаблону",
        "context": "",
        "context_before": "",
        "context_highlight": "",
        "context_after": "",
        "suggestion": suggestion,
        "fixable": fixable,
    }


def _summary(issues):
    counts = Counter(item["severity"] for item in issues)
    return {
        "info": counts.get("info", 0),
        "warning": counts.get("warning", 0),
        "error": counts.get("error", 0),
        "critical": counts.get("critical", 0),
        "total": len(issues),
    }


def _payload(submission, message, issues, *, metrics=None, execution_status=""):
    payload = {
        "schema_version": "1.0",
        "check_code": "formatting_compliance",
        "message": message,
        "summary": _summary(issues),
        "issues": issues,
        "metrics": metrics or {},
        "extracted_metadata": {},
        "details": {
            "template_id": submission.formatting_template_id,
            "template_version": (
                submission.formatting_template.version_number
                if submission.formatting_template_id
                else None
            ),
            "rule_sources": (submission.formatting_rules_snapshot or {}).get("sources") or [],
            "rule_conflicts": (submission.formatting_rules_snapshot or {}).get("conflicts") or [],
            "can_generate_corrected_document": any(item.get("fixable") for item in issues),
        },
    }
    if execution_status:
        payload["execution_status"] = execution_status
    return payload


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _different(left, right, tolerance):
    left_value = _as_float(left)
    right_value = _as_float(right)
    if left_value is None or right_value is None:
        return False
    return abs(left_value - right_value) > tolerance


def _dominant(values):
    cleaned = [value for value in values if value not in (None, "")]
    return Counter(cleaned).most_common(1)[0][0] if cleaned else None


def build_formatting_compliance_report(submission, version):
    if not submission.formatting_check_requested:
        return True, _payload(
            submission,
            "Автор отключил необязательную проверку оформления.",
            [],
            execution_status="not_performed",
        )
    if submission.formatting_template_id is None:
        return True, _payload(
            submission,
            "Шаблон оформления не приложен. Остальные автоматические проверки выполнены.",
            [],
            execution_status="not_performed",
        )

    rules = (submission.formatting_rules_snapshot or {}).get("effective") or {}
    if not rules:
        return True, _payload(
            submission,
            "Из шаблона пока не удалось извлечь проверяемые правила.",
            [],
            execution_status="partial",
        )

    with version.file.open("rb") as source:
        data = read_file_bytes(source)
    suffix = Path(version.file.name).suffix.casefold()
    snapshot = analyze_document_bytes(data, version.file.name)
    issues = []
    metrics = {}

    limits = rules.get("limits") or {}
    word_count = len(WORD_RE.findall(snapshot.get("text") or ""))
    metrics["word_count"] = word_count
    minimum_words = _as_float(limits.get("min_words"))
    maximum_words = _as_float(limits.get("max_words"))
    if minimum_words is not None and word_count and word_count < minimum_words:
        issues.append(
            _issue(
                "template_min_words",
                "Объём меньше требования шаблона",
                f"Найдено {word_count} слов, требуется не менее {int(minimum_words)}.",
                suggestion="Дополните материал самостоятельно; система не придумывает научное содержание.",
            )
        )
    if maximum_words is not None and word_count > maximum_words:
        issues.append(
            _issue(
                "template_max_words",
                "Объём больше требования шаблона",
                f"Найдено {word_count} слов, допускается не более {int(maximum_words)}.",
                suggestion="Сократите материал вручную, сохранив научный смысл.",
            )
        )

    normalized_paragraphs = {
        normalize_for_match(item.get("text", "")).strip(" .:")
        for item in snapshot.get("paragraphs") or []
    }
    required_sections = ((rules.get("structure") or {}).get("required_sections") or [])
    missing_sections = [
        section
        for section in required_sections
        if normalize_for_match(section).strip(" .:") not in normalized_paragraphs
    ]
    for section in missing_sections:
        issues.append(
            _issue(
                "template_missing_section",
                f"Нет раздела «{section}»",
                "Обязательный раздел из выбранного шаблона не найден.",
                suggestion="Добавьте раздел и заполните его собственным научным содержанием.",
            )
        )
    metrics["required_sections"] = required_sections
    metrics["missing_sections"] = missing_sections

    if suffix != ".docx":
        issues.append(
            _issue(
                "template_limited_format_analysis",
                "Форматирование проверено частично",
                "Точные поля, шрифты и интервалы можно надёжно проверить и исправить только в DOCX.",
                severity="info",
                suggestion="При необходимости загрузите работу в формате DOCX.",
            )
        )
        return True, _payload(
            submission,
            "Структурные требования проверены, форматирование — частично.",
            issues,
            metrics=metrics,
            execution_status="partial",
        )

    try:
        from docx import Document

        document = Document(io.BytesIO(data))
    except Exception:
        issues.append(
            _issue(
                "template_docx_open_failed",
                "Не удалось прочитать форматирование DOCX",
                "Файл сохранён, но его стили и параметры страницы не распознаны.",
                severity="info",
            )
        )
        return True, _payload(
            submission,
            "Проверка оформления выполнена частично.",
            issues,
            metrics=metrics,
            execution_status="partial",
        )

    page_rules = rules.get("page") or {}
    margins = page_rules.get("margins_cm") or {}
    if document.sections:
        section = document.sections[0]
        actual_margins = {
            "top": round(section.top_margin.cm, 2),
            "right": round(section.right_margin.cm, 2),
            "bottom": round(section.bottom_margin.cm, 2),
            "left": round(section.left_margin.cm, 2),
        }
        metrics["margins_cm"] = actual_margins
        margin_labels = {
            "top": "верхнее",
            "right": "правое",
            "bottom": "нижнее",
            "left": "левое",
        }
        for key, label in margin_labels.items():
            if _different(actual_margins.get(key), margins.get(key), 0.12):
                issues.append(
                    _issue(
                        f"template_margin_{key}",
                        f"Неверное {label} поле",
                        f"В документе {actual_margins[key]} см, в шаблоне {margins[key]} см.",
                        suggestion="Система может выставить поле автоматически.",
                        fixable=True,
                    )
                )

    body_rules = rules.get("body") or {}
    normal_style = document.styles["Normal"]
    default_font = normal_style.font.name or ""
    default_size = round(normal_style.font.size.pt, 1) if normal_style.font.size else None
    fonts = []
    sizes = []
    line_spacings = []
    first_line_indents = []
    for paragraph in document.paragraphs:
        if not paragraph.text.strip():
            continue
        style_name = (paragraph.style.name or "").casefold() if paragraph.style else ""
        if "heading" in style_name or "заголов" in style_name:
            continue
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            fonts.append(run.font.name or default_font)
            sizes.append(round(run.font.size.pt, 1) if run.font.size else default_size)
        spacing = paragraph.paragraph_format.line_spacing
        if isinstance(spacing, (int, float)):
            line_spacings.append(round(float(spacing), 2))
        indent = paragraph.paragraph_format.first_line_indent
        if indent is not None:
            first_line_indents.append(round(indent.cm, 2))

    actual_font = _dominant(fonts)
    actual_size = _dominant(sizes)
    actual_spacing = _dominant(line_spacings)
    actual_indent = _dominant(first_line_indents)
    metrics.update(
        {
            "font_family": actual_font,
            "font_size_pt": actual_size,
            "line_spacing": actual_spacing,
            "first_line_indent_cm": actual_indent,
        }
    )
    expected_font = str(body_rules.get("font_family") or "").strip()
    if expected_font and actual_font and expected_font.casefold() != str(actual_font).casefold():
        issues.append(
            _issue(
                "template_font_family",
                "Основной шрифт отличается",
                f"Преобладает «{actual_font}», шаблон требует «{expected_font}».",
                suggestion="Система может заменить основной шрифт автоматически.",
                fixable=True,
            )
        )
    if _different(actual_size, body_rules.get("font_size_pt"), 0.2):
        issues.append(
            _issue(
                "template_font_size",
                "Размер основного шрифта отличается",
                f"Преобладает {actual_size} пт, шаблон требует {body_rules.get('font_size_pt')} пт.",
                suggestion="Система может изменить размер автоматически.",
                fixable=True,
            )
        )
    if _different(actual_spacing, body_rules.get("line_spacing"), 0.05):
        issues.append(
            _issue(
                "template_line_spacing",
                "Межстрочный интервал отличается",
                f"Распознано {actual_spacing or 'не задано'}, шаблон требует {body_rules.get('line_spacing')}.",
                suggestion="Система может выставить интервал автоматически.",
                fixable=True,
            )
        )
    if _different(actual_indent, body_rules.get("first_line_indent_cm"), 0.08):
        issues.append(
            _issue(
                "template_first_line_indent",
                "Абзацный отступ отличается",
                f"Распознано {actual_indent or 0} см, шаблон требует {body_rules.get('first_line_indent_cm')} см.",
                suggestion="Система может выставить отступ автоматически.",
                fixable=True,
            )
        )

    message = (
        "Оформление соответствует извлечённым правилам шаблона."
        if not issues
        else f"По шаблону найдено замечаний: {len(issues)}."
    )
    return not any(item["severity"] in {"error", "critical"} for item in issues), _payload(
        submission,
        message,
        issues,
        metrics=metrics,
    )
