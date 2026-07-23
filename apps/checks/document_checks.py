import re
from collections import Counter
from pathlib import Path

from django.conf import settings

from apps.submissions.document_analysis import (
    CITATION_RE,
    DOI_LABEL_RE,
    DOI_RE,
    EDN_LABEL_RE,
    EMAIL_RE,
    FIGURE_CAPTION_RE,
    FIGURE_REFERENCE_RE,
    ORCID_LABEL_RE,
    SECTION_ALIASES,
    SUPPORTED_EXTENSIONS,
    TABLE_CAPTION_RE,
    TABLE_REFERENCE_RE,
    URL_RE,
    analyze_document_bytes,
    normalize_for_match,
    normalize_space,
    read_file_bytes,
)


SEVERITIES = ("info", "warning", "error", "critical")
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’-]*")
MIXED_SCRIPT_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")
EQUATION_LABEL_RE = re.compile(r"^\(?\s*(\d{1,3})\s*\)?$")
DEFAULT_WORD_LIMITS = {
    "article": (2000, 12000),
    "monograph": (10000, 200000),
    "theses": (500, 5000),
}


def _issue(
    code,
    title,
    severity,
    message,
    *,
    location="",
    context="",
    highlight="",
    suggestion="",
):
    severity = severity if severity in SEVERITIES else "warning"
    context_before = context_after = ""
    if context and highlight:
        index = normalize_for_match(context).find(normalize_for_match(highlight))
        if index >= 0:
            context_before = context[:index]
            context_after = context[index + len(highlight) :]
        else:
            context_before = context
            highlight = ""
    elif context:
        context_before = context
    return {
        "code": code,
        "title": title,
        "severity": severity,
        "message": message,
        "location": location,
        "context": context,
        "context_before": context_before,
        "context_highlight": highlight,
        "context_after": context_after,
        "suggestion": suggestion,
    }


def _summarize(issues):
    counts = Counter(issue["severity"] for issue in issues)
    return {severity: counts.get(severity, 0) for severity in SEVERITIES} | {"total": len(issues)}


def _build_payload(check_code, message, issues, *, metrics=None, extracted_metadata=None, details=None):
    return {
        "schema_version": "1.0",
        "check_code": check_code,
        "message": message,
        "summary": _summarize(issues),
        "issues": issues,
        "metrics": metrics or {},
        "extracted_metadata": extracted_metadata or {},
        "details": details or {},
    }


def _is_success(issues):
    return not any(issue["severity"] in {"error", "critical"} for issue in issues)


def build_snapshot(version):
    if version is None or not version.file:
        return analyze_document_bytes(b"", "")
    with version.file.open("rb") as source:
        data = read_file_bytes(source)
    return analyze_document_bytes(data, version.file.name)


def _metadata_value(submission, snapshot, field_name):
    value = normalize_space(getattr(submission, field_name, "") or "")
    if value:
        return value
    return normalize_space((snapshot.get("metadata") or {}).get(field_name, "") or "")


def _find_section(paragraphs, aliases):
    normalized_aliases = {normalize_for_match(alias) for alias in aliases}
    for index, paragraph in enumerate(paragraphs):
        normalized = normalize_for_match(paragraph["text"]).strip(" .:")
        if normalized in normalized_aliases:
            return index, paragraph
    return None, None


def _journal_policy(submission):
    policy = getattr(submission.journal, "editorial_policy", None)
    return policy if isinstance(policy, dict) else {}


def _required_sections(submission):
    # Required sections are checked by the selected formatting template.  This
    # legacy quality check deliberately does not impose a generic IMRAD layout.
    return []


def _word_limits(submission):
    article_type = submission.article_type
    minimum = getattr(article_type, "min_word_count", None)
    maximum = getattr(article_type, "max_word_count", None)
    policy = _journal_policy(submission)
    if isinstance(policy.get("min_words"), int):
        minimum = policy["min_words"]
    if isinstance(policy.get("max_words"), int):
        maximum = policy["max_words"]
    if minimum is None or maximum is None:
        default_minimum, default_maximum = DEFAULT_WORD_LIMITS.get(article_type.code, (500, 100000))
        minimum = default_minimum if minimum is None else minimum
        maximum = default_maximum if maximum is None else maximum
    return minimum, maximum


def _context_around(text, start, end, radius=105):
    before = max(0, start - radius)
    after = min(len(text), end + radius)
    prefix = "…" if before else ""
    suffix = "…" if after < len(text) else ""
    return prefix + normalize_space(text[before:after]) + suffix


def _mixed_script_issues(text):
    issues = []
    for match in MIXED_SCRIPT_RE.finditer(text):
        token = match.group(0)
        if not (re.search(r"[A-Za-z]", token) and re.search(r"[А-Яа-яЁё]", token)):
            continue
        context = _context_around(text, match.start(), match.end())
        issues.append(
            _issue(
                "mixed_alphabet_token",
                "Смешение кириллицы и латиницы",
                "warning",
                f"В одном слове смешаны символы разных алфавитов: «{token}».",
                location="Текст документа",
                context=context,
                highlight=token,
                suggestion="Проверьте написание: такая подмена бывает случайной или используется для маскировки текста.",
            )
        )
        if len(issues) >= 20:
            break
    return issues


def _orcid_is_valid(value):
    normalized = value.replace("https://orcid.org/", "").replace("-", "").upper()
    if not re.fullmatch(r"\d{15}[\dX]", normalized):
        return False
    total = 0
    for char in normalized[:15]:
        total = (total + int(char)) * 2
    remainder = (12 - total % 11) % 11
    expected = "X" if remainder == 10 else str(remainder)
    return normalized[-1] == expected


def _identifier_issues(text, contact_emails):
    issues = []
    for value in re.split(r"[,;\s]+", contact_emails or ""):
        value = value.strip()
        if value and not EMAIL_RE.fullmatch(value):
            issues.append(
                _issue(
                    "invalid_email",
                    "Некорректный e-mail",
                    "error",
                    f"Адрес «{value}» не соответствует формату e-mail.",
                    location="Метаданные",
                    highlight=value,
                    context=value,
                    suggestion="Исправьте адрес или удалите лишние символы.",
                )
            )

    for match in DOI_LABEL_RE.finditer(text):
        value = (match.group(1) or "").strip().rstrip(".,)")
        if not value:
            issues.append(
                _issue(
                    "empty_doi_label",
                    "DOI не заполнен",
                    "info",
                    "В документе есть поле DOI, но значение не указано.",
                    location="Метаданные документа",
                    context=_context_around(text, match.start(), match.end(), 70),
                    highlight=match.group(0),
                )
            )
        elif value.lower().startswith("https://doi.org/"):
            doi_value = value.split("doi.org/", 1)[1]
            if not DOI_RE.fullmatch(doi_value):
                issues.append(_issue("invalid_doi", "Некорректный DOI", "warning", f"Проверьте DOI «{value}».", context=value, highlight=value))
        elif value.startswith("10.") and not DOI_RE.fullmatch(value):
            issues.append(_issue("invalid_doi", "Некорректный DOI", "warning", f"Проверьте DOI «{value}».", context=value, highlight=value))

    for match in URL_RE.finditer(text):
        value = match.group(0).rstrip(".,;)")
        parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(_issue("invalid_url", "Некорректный URL", "warning", f"Проверьте ссылку «{value}».", context=value, highlight=value))

    for match in ORCID_LABEL_RE.finditer(text):
        value = (match.group(1) or "").strip().rstrip(".,;)")
        if value and not _orcid_is_valid(value):
            issues.append(_issue("invalid_orcid", "Некорректный ORCID", "warning", f"ORCID «{value}» не прошёл проверку контрольной цифры.", context=value, highlight=value))

    for match in EDN_LABEL_RE.finditer(text):
        value = (match.group(1) or "").strip().rstrip(".,;)")
        if value and not re.fullmatch(r"[A-Z]{5,10}", value):
            issues.append(_issue("invalid_edn", "Некорректный EDN", "warning", f"Проверьте EDN «{value}».", context=value, highlight=value))
    return issues


def _classify_tables(snapshot):
    equation_labels = []
    data_tables = []
    for table in snapshot.get("tables") or []:
        rows = table.get("rows") or []
        flat = [normalize_space(cell) for row in rows for cell in row if normalize_space(cell)]
        labels = [EQUATION_LABEL_RE.fullmatch(cell) for cell in flat]
        numeric_labels = [match.group(1) for match in labels if match]
        if len(rows) <= 2 and len(flat) <= 4 and numeric_labels:
            equation_labels.extend(numeric_labels)
        else:
            data_tables.append(table)
    return data_tables, sorted(set(equation_labels), key=int)


def build_document_quality_report(submission, version, *, snapshot=None):
    snapshot = snapshot or build_snapshot(version)
    issues = []
    metadata = snapshot.get("metadata") or {}
    text = snapshot.get("text") or ""
    paragraphs = snapshot.get("paragraphs") or []

    document_authors = _metadata_value(submission, snapshot, "document_authors")
    organizations = _metadata_value(submission, snapshot, "organizations")
    contact_emails = _metadata_value(submission, snapshot, "contact_emails")
    keywords = _metadata_value(submission, snapshot, "keywords")
    metadata_emails = [value for value in re.split(r"[,;\s]+", contact_emails) if value]
    unique_emails = list(dict.fromkeys(value.casefold() for value in metadata_emails))
    if len(unique_emails) > 1:
        issues.append(
            _issue(
                "multiple_contact_emails",
                "Найдены разные контактные e-mail",
                "warning",
                "В документе распознано несколько разных адресов: " + ", ".join(metadata_emails) + ".",
                location="Метаданные",
                context=", ".join(metadata_emails),
                suggestion="Проверьте, нет ли опечатки, и оставьте актуальные адреса.",
            )
        )

    reference_index, _reference_heading = _find_section(
        paragraphs,
        SECTION_ALIASES["Список литературы"],
    )
    references = []
    if reference_index is not None:
        references = [item["text"] for item in paragraphs[reference_index + 1 :] if len(item["text"]) > 20]
    reference_policy = _journal_policy(submission)
    references_required = bool(
        reference_policy.get("references_required")
        or (reference_policy.get("minimum_references") or 0) > 0
    )
    if references_required and not references:
        issues.append(
            _issue(
                "missing_references",
                "Не найден список литературы",
                "error",
                "Обязательный список литературы отсутствует или не распознан.",
                location="Структура документа",
                suggestion="Добавьте отдельный раздел «Список литературы».",
            )
        )

    word_count = len(WORD_RE.findall(text))
    minimum_words, maximum_words = _word_limits(submission)
    found_sections = []
    missing_sections = []
    for required in _required_sections(submission):
        aliases = SECTION_ALIASES.get(required, (required,))
        index, paragraph = _find_section(paragraphs, aliases)
        if paragraph is None:
            missing_sections.append(required)
            issues.append(_issue("missing_required_section", f"Нет раздела «{required}»", "warning", f"Раздел, обязательный политикой журнала, не найден: «{required}».", location="Структура документа", suggestion="Добавьте раздел или измените политику журнала в админке, если он не требуется."))
        else:
            found_sections.append(required)
            style = normalize_for_match(paragraph.get("style", ""))
            if "heading" not in style and "заголов" not in style:
                issues.append(_issue("section_not_heading", "Раздел оформлен не как заголовок", "warning", f"«{required}» найден, но стиль абзаца — «{paragraph.get('style') or 'Обычный'}».", location=f"Абзац {paragraph['index'] + 1}", context=paragraph["text"], highlight=paragraph["text"], suggestion="Примените согласованный стиль заголовка."))

    heading_levels = []
    for paragraph in paragraphs:
        style = normalize_for_match(paragraph.get("style", ""))
        level_match = re.search(r"(?:heading|заголовок)\s*(\d+)", style)
        if level_match:
            heading_levels.append((int(level_match.group(1)), paragraph))
    for previous, current in zip(heading_levels, heading_levels[1:]):
        if current[0] - previous[0] > 1:
            issues.append(_issue("heading_level_jump", "Нарушена иерархия заголовков", "warning", f"После заголовка уровня {previous[0]} сразу используется уровень {current[0]}.", location=f"Абзац {current[1]['index'] + 1}", context=current[1]["text"], highlight=current[1]["text"], suggestion="Не пропускайте уровни заголовков."))

    figure_captions = {}
    table_captions = {}
    for paragraph in paragraphs:
        figure_match = FIGURE_CAPTION_RE.match(paragraph["text"])
        table_match = TABLE_CAPTION_RE.match(paragraph["text"])
        if figure_match:
            figure_captions[figure_match.group(1)] = paragraph
        if table_match:
            table_captions[table_match.group(1)] = paragraph

    if snapshot.get("image_count", 0) > len(figure_captions):
        issues.append(_issue("missing_figure_caption", "Не у всех рисунков есть подписи", "error", f"В файле найдено изображений: {snapshot.get('image_count', 0)}, распознано подписей: {len(figure_captions)}.", location="Рисунки", suggestion="Добавьте нумерованную подпись к каждому содержательному рисунку."))
    if figure_captions:
        figure_numbers = sorted(int(value) for value in figure_captions)
        expected_figure_numbers = list(range(1, max(figure_numbers) + 1))
        if figure_numbers != expected_figure_numbers:
            issues.append(_issue("figure_numbering_gap", "Нарушена нумерация рисунков", "warning", f"Распознаны номера: {', '.join(map(str, figure_numbers))}.", location="Рисунки", suggestion="Используйте последовательную нумерацию с 1 без пропусков."))
    text_without_figure_captions = "\n".join(item["text"] for item in paragraphs if not FIGURE_CAPTION_RE.match(item["text"]))
    figure_references = set(FIGURE_REFERENCE_RE.findall(text_without_figure_captions))
    for number, paragraph in figure_captions.items():
        if number not in figure_references:
            issues.append(_issue("unreferenced_figure", f"Нет ссылки на рисунок {number}", "warning", f"Подпись «{paragraph['text']}» есть, но ссылка на рисунок в тексте не найдена.", location=f"Рисунок {number}", context=paragraph["text"], highlight=f"Рис. {number}", suggestion="Добавьте ссылку на рисунок в основном тексте."))

    data_tables, equation_labels = _classify_tables(snapshot)
    if len(data_tables) > len(table_captions):
        issues.append(_issue("missing_table_caption", "Не у всех таблиц есть подписи", "error", f"Содержательных таблиц: {len(data_tables)}, распознано подписей: {len(table_captions)}.", location="Таблицы", suggestion="Добавьте номер и название каждой таблицы."))
    if table_captions:
        table_numbers = sorted(int(value) for value in table_captions)
        expected_table_numbers = list(range(1, max(table_numbers) + 1))
        if table_numbers != expected_table_numbers:
            issues.append(_issue("table_numbering_gap", "Нарушена нумерация таблиц", "warning", f"Распознаны номера: {', '.join(map(str, table_numbers))}.", location="Таблицы", suggestion="Используйте последовательную нумерацию с 1 без пропусков."))
    text_without_table_captions = "\n".join(item["text"] for item in paragraphs if not TABLE_CAPTION_RE.match(item["text"]))
    table_references = set(TABLE_REFERENCE_RE.findall(text_without_table_captions))
    for number, paragraph in table_captions.items():
        if number not in table_references:
            issues.append(_issue("unreferenced_table", f"Нет ссылки на таблицу {number}", "warning", f"Таблица {number} подписана, но ссылка на неё в тексте не найдена.", location=f"Таблица {number}", context=paragraph["text"], highlight=f"Таблица {number}", suggestion="Добавьте ссылку на таблицу в основном тексте."))

    formula_references = set()
    for number in equation_labels:
        pattern = re.compile(rf"(?:формул|уравнен|выражен)[^\n]{{0,60}}\(?\s*{re.escape(number)}\s*\)?", re.I)
        if pattern.search(text):
            formula_references.add(number)
    unreferenced_formulas = [number for number in equation_labels if number not in formula_references]
    if unreferenced_formulas:
        issues.append(_issue("unreferenced_formulas", "Не найдены явные ссылки на формулы", "warning", "Нет текстовых ссылок на формулы: " + ", ".join(unreferenced_formulas) + ".", location="Формулы", suggestion="Если политика журнала требует, добавьте фразы вида «в формуле (3)»."))

    if reference_index is not None and references:
        body_text = "\n".join(item["text"] for item in paragraphs[:reference_index])
        cited_numbers = set()
        for citation in CITATION_RE.findall(body_text):
            cited_numbers.update(int(value) for value in re.findall(r"\d+", citation))
        available_numbers = set(range(1, len(references) + 1))
        missing_sources = sorted(cited_numbers - available_numbers)
        if missing_sources:
            issues.append(_issue("citation_without_source", "Ссылки без источников", "error", "В тексте есть ссылки на отсутствующие пункты списка: " + ", ".join(map(str, missing_sources)) + ".", location="Список литературы", suggestion="Добавьте источники или исправьте номера ссылок."))
        if _journal_policy(submission).get("disallow_uncited_references"):
            uncited = sorted(available_numbers - cited_numbers)
            if uncited:
                issues.append(_issue("uncited_sources", "Источники без ссылок в тексте", "warning", "Политика журнала запрещает несвязанные источники. Не процитированы: " + ", ".join(map(str, uncited)) + ".", location="Список литературы", suggestion="Добавьте ссылки в текст или удалите лишние источники."))
    else:
        cited_numbers = set()

    issues.extend(_mixed_script_issues(text))
    issues.extend(_identifier_issues(text, contact_emails))

    metrics = {
        "word_count": word_count,
        "minimum_words": minimum_words,
        "maximum_words": maximum_words,
        "sections_found": found_sections,
        "sections_missing": missing_sections,
        "figures": snapshot.get("image_count", 0),
        "figure_captions": len(figure_captions),
        "tables": len(data_tables),
        "table_captions": len(table_captions),
        "formulas": len(equation_labels),
        "references": len(references),
        "citations": len(cited_numbers),
    }
    summary = _summarize(issues)
    message = (
        f"Проверена целостность документа: {summary['error'] + summary['critical']} ошибок, "
        f"{summary['warning']} предупреждений. Обязательные поля и структура проверяются "
        "только по выбранному шаблону."
    )
    payload = _build_payload("metadata_complete", message, issues, metrics=metrics, extracted_metadata=metadata)
    return _is_success(issues), payload


def build_file_safety_report(submission, version, *, snapshot=None):
    snapshot = snapshot or build_snapshot(version)
    issues = []
    suffix = snapshot.get("suffix", "")
    size = snapshot.get("size", 0)
    maximum_size = int(getattr(settings, "SUBMISSION_FILE_MAX_SIZE", 50 * 1024 * 1024))
    magic_hex = snapshot.get("magic_hex", "")

    if version is None or not version.file:
        issues.append(_issue("file_missing", "Файл отсутствует", "critical", "У текущей версии нет файла.", location="Файл"))
    if suffix not in SUPPORTED_EXTENSIONS:
        issues.append(_issue("unsupported_file_type", "Недопустимый формат", "critical", f"Формат «{suffix or 'без расширения'}» не разрешён.", location="Файл", suggestion="Используйте DOCX, DOC, PDF, TXT, MD или RTF."))
    if size > maximum_size:
        issues.append(_issue("file_too_large", "Превышен размер файла", "critical", f"Размер файла {round(size / 1024 / 1024, 1)} МБ, лимит — {round(maximum_size / 1024 / 1024)} МБ.", location="Файл"))

    signature_valid = True
    if suffix == ".docx":
        signature_valid = magic_hex.startswith("504b")
    elif suffix == ".pdf":
        signature_valid = magic_hex.startswith("25504446")
    elif suffix == ".doc":
        signature_valid = magic_hex.startswith("d0cf11e0a1b11ae1")
    if not signature_valid:
        issues.append(
            _issue(
                "file_signature_mismatch",
                "Расширение не соответствует содержимому",
                "critical",
                f"Файл имеет расширение «{suffix}», но его сигнатура не соответствует формату.",
                location="Файл",
                suggestion="Не переименовывайте файл вручную; пересохраните его в нужном формате.",
            )
        )

    if suffix == ".docx":
        if snapshot.get("dangerous_members"):
            for member in snapshot["dangerous_members"][:20]:
                issues.append(_issue("dangerous_archive_member", "Потенциально опасное вложение", "critical", f"В контейнере DOCX найден объект «{member}».", location="Внутри DOCX", context=member, highlight=member, suggestion="Удалите макрос, OLE-объект или исполняемое вложение."))
        if snapshot.get("dangerous_relationships"):
            for target in snapshot["dangerous_relationships"][:20]:
                issues.append(_issue("dangerous_external_link", "Опасная внешняя связь", "critical", f"Документ ссылается на локальный или исполняемый ресурс: «{target}».", location="Связи DOCX", context=target, highlight=target))
        if snapshot.get("compression_ratio", 1) > 150 or snapshot.get("unpacked_size", 0) > 300 * 1024 * 1024:
            issues.append(_issue("archive_bomb_risk", "Подозрительное сжатие архива", "critical", f"Коэффициент распаковки DOCX: {snapshot.get('compression_ratio')}.", location="Контейнер DOCX", suggestion="Пересохраните документ в Word без встроенных объектов."))
    elif suffix == ".doc":
        issues.append(_issue("legacy_doc_format", "Устаревший формат DOC", "warning", "Бинарный DOC нельзя полностью проверить на макросы и вложения без конвертации.", location="Файл", suggestion="Пересохраните материал в DOCX."))

    parse_error = snapshot.get("parse_error") or ""
    if parse_error:
        severity = "error" if suffix not in {".doc", ".pdf"} else "warning"
        issues.append(_issue("limited_or_failed_parsing", "Структура проверена не полностью", severity, parse_error, location="Файл", suggestion="Для полной проверки используйте корректный DOCX."))

    summary = _summarize(issues)
    message = (
        "Формат, размер и внутренние вложения проверены. "
        + ("Опасных объектов не найдено." if not any(item["severity"] == "critical" for item in issues) else "Найдены потенциально опасные объекты.")
    )
    payload = _build_payload(
        "file_uploaded",
        message,
        issues,
        metrics={
            "file_name": snapshot.get("file_name"),
            "extension": suffix,
            "size_bytes": size,
            "signature_valid": signature_valid,
            "maximum_size_bytes": maximum_size,
            "archive_members": len(snapshot.get("archive_members") or []),
            "external_relationships": len(snapshot.get("external_relationships") or []),
        },
        details={"external_relationships": snapshot.get("external_relationships") or []},
    )
    return _is_success(issues), payload
