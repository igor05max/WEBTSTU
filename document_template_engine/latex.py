from __future__ import annotations

import re
from typing import Any

from .schema import BLOCK_CATALOG, get_document_blocks, normalize_template_rules


_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’-]*")
_DIMENSION_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>cm|mm|in|pt)\b",
    re.I,
)
_COMMAND_WITH_VALUE_RE = re.compile(
    r"\\(?P<name>[A-Za-z@]+)\*?(?:\[[^\]]*\])?\{(?P<value>(?:[^{}]|\{[^{}]*\})*)\}",
    re.S,
)


def _decode_source(source: bytes | str) -> str:
    if isinstance(source, str):
        return source
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "utf-16le"):
        try:
            return source.decode(encoding)
        except UnicodeDecodeError:
            continue
    return source.decode("utf-8", errors="replace")


def _strip_comments(source: str) -> str:
    lines = []
    for line in source.splitlines():
        escaped = False
        output = []
        for character in line:
            if character == "%" and not escaped:
                break
            output.append(character)
            if character == "\\":
                escaped = not escaped
            else:
                escaped = False
        lines.append("".join(output))
    return "\n".join(lines)


def _command_values(source: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for match in _COMMAND_WITH_VALUE_RE.finditer(source):
        values.setdefault(match.group("name").casefold(), []).append(
            match.group("value").strip()
        )
    return values


def _dimension_cm(value: str) -> float | None:
    match = _DIMENSION_RE.search(str(value or ""))
    if not match:
        return None
    number = float(match.group("value").replace(",", "."))
    unit = match.group("unit").casefold()
    multiplier = {
        "cm": 1.0,
        "mm": 0.1,
        "in": 2.54,
        "pt": 2.54 / 72.27,
    }[unit]
    return round(number * multiplier, 2)


def _normalized_heading(value: str) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return " ".join(text.split())


def _unescape_latex(value: str) -> str:
    replacements = {
        r"\%": "%",
        r"\&": "&",
        r"\#": "#",
        r"\_": "_",
        r"\{": "{",
        r"\}": "}",
        r"\textbackslash{}": "\\",
        "~": " ",
    }
    result = value
    for source, replacement in replacements.items():
        result = result.replace(source, replacement)
    return result


def latex_to_plain_text(source: bytes | str) -> str:
    """Return readable document text without executing any LaTeX commands."""

    text = _strip_comments(_decode_source(source))
    source_commands = _command_values(text)
    front_matter = []
    for command in (
        "udc",
        "title",
        "author",
        "authors",
        "supervisor",
        "institution",
        "citycountry",
        "abstract",
        "keywords",
    ):
        front_matter.extend(source_commands.get(command) or [])
    document_match = re.search(
        r"\\begin\s*\{document\}(?P<body>.*?)\\end\s*\{document\}",
        text,
        re.S | re.I,
    )
    if document_match:
        text = document_match.group("body")

    for environment in ("abstract", "center", "flushleft", "flushright", "quote"):
        text = re.sub(
            rf"\\(?:begin|end)\s*\{{{environment}\}}",
            "\n",
            text,
            flags=re.I,
        )
    text = re.sub(
        r"\\(?:section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\{([^{}]*)\}",
        r"\n\1\n",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\\(?:title|author|udc|supervisor|institution|citycountry|keywords)"
        r"\*?(?:\[[^\]]*\])?\{([^{}]*)\}",
        r"\n\1\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"\\(?:textbf|textit|emph|underline|MakeUppercase)\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:begin|end)\s*\{[^{}]+\}(?:\[[^\]]*\])?", "\n", text)
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"[{}$]", " ", text)
    text = _unescape_latex(text)
    visible_text = "\n".join(
        " ".join(line.split())
        for line in text.splitlines()
        if " ".join(line.split())
    )
    return "\n".join(
        value
        for value in [
            *(" ".join(item.split()) for item in front_matter if " ".join(item.split())),
            visible_text,
        ]
        if value
    )


def _geometry_options(source: str) -> dict[str, str]:
    chunks = re.findall(r"\\geometry\s*\{([^}]*)\}", source, re.S | re.I)
    chunks.extend(
        match.group(1)
        for match in re.finditer(
            r"\\usepackage\s*\[([^\]]*)\]\s*\{geometry\}",
            source,
            re.S | re.I,
        )
    )
    options: dict[str, str] = {}
    for chunk in chunks:
        for item in chunk.split(","):
            key, separator, value = item.partition("=")
            normalized_key = key.strip().casefold()
            if not normalized_key:
                continue
            options[normalized_key] = value.strip() if separator else "true"
    return options


def _document_prefix(source: str, limit: int = 1200) -> str:
    match = re.search(r"\\begin\s*\{document\}", source, re.I)
    if not match:
        return ""
    return source[match.end() : match.end() + limit]


def extract_latex_template_rules(source: bytes | str) -> dict[str, Any]:
    """Extract deterministic layout rules from a safe, non-executed TeX source."""

    text = _strip_comments(_decode_source(source))
    commands = _command_values(text)
    result: dict[str, Any] = {}

    class_match = re.search(
        r"\\documentclass(?:\[([^\]]*)\])?\s*\{([^}]+)\}",
        text,
        re.S | re.I,
    )
    class_options = []
    if class_match:
        class_options = [
            value.strip().casefold()
            for value in (class_match.group(1) or "").split(",")
            if value.strip()
        ]

    geometry = _geometry_options(text)
    page: dict[str, Any] = {}
    if "a4paper" in class_options or "a4paper" in geometry or geometry.get("paper") == "a4paper":
        page["size"] = "A4"
    if "landscape" in class_options or "landscape" in geometry:
        page["orientation"] = "landscape"
    elif class_match or geometry:
        page["orientation"] = "portrait"

    margins: dict[str, float] = {}
    common_margin = _dimension_cm(geometry.get("margin", ""))
    if common_margin is not None:
        margins = {key: common_margin for key in ("top", "right", "bottom", "left")}
    for key, aliases in {
        "top": ("top",),
        "right": ("right", "outer"),
        "bottom": ("bottom",),
        "left": ("left", "inner"),
    }.items():
        for alias in aliases:
            parsed = _dimension_cm(geometry.get(alias, ""))
            if parsed is not None:
                margins[key] = parsed
                break
    if margins:
        page["margins_cm"] = margins
    if page:
        result["page"] = page

    body: dict[str, Any] = {}
    font_match = re.search(
        r"\\setmainfont(?:\[[^\]]*\])?\s*\{([^}]+)\}",
        text,
        re.S | re.I,
    )
    if font_match:
        body["font_family"] = " ".join(font_match.group(1).split())
    elif re.search(r"\\usepackage(?:\[[^\]]*\])?\s*\{(?:newtxtext|mathptmx|times)\}", text, re.I):
        body["font_family"] = "Times New Roman"

    document_prefix = _document_prefix(text)
    size_match = re.search(
        r"\\AtBeginDocument\s*\{[^{}]*\\fontsize\s*\{(\d+(?:[.,]\d+)?)\s*(?:pt)?\}",
        text,
        re.S | re.I,
    )
    if not size_match:
        size_match = re.match(
            r"(?:\s|\\(?:selectfont|normalfont)\b)*"
            r"\\fontsize\s*\{(\d+(?:[.,]\d+)?)\s*(?:pt)?\}",
            document_prefix,
            re.S | re.I,
        )
    if size_match:
        body["font_size_pt"] = float(size_match.group(1).replace(",", "."))
    else:
        for option in class_options:
            match = re.fullmatch(r"(\d+(?:[.,]\d+)?)pt", option)
            if match:
                body["font_size_pt"] = float(match.group(1).replace(",", "."))
                break

    spacing_match = re.search(r"\\setstretch\s*\{(\d+(?:[.,]\d+)?)\}", text, re.I)
    if spacing_match:
        body["line_spacing"] = float(spacing_match.group(1).replace(",", "."))
    elif re.search(r"\\onehalfspacing\b", text, re.I):
        body["line_spacing"] = 1.5
    elif re.search(r"\\doublespacing\b", text, re.I):
        body["line_spacing"] = 2.0
    elif re.search(r"\\singlespacing\b", text, re.I):
        body["line_spacing"] = 1.0

    indent_match = re.search(
        r"\\setlength\s*\{\\parindent\}\s*\{([^}]+)\}",
        text,
        re.S | re.I,
    )
    if indent_match:
        indent = _dimension_cm(indent_match.group(1))
        if indent is not None:
            body["first_line_indent_cm"] = indent
    begin_alignment_match = re.search(
        r"\\AtBeginDocument\s*\{[^{}]*\\(raggedright|centering|justifying)\b",
        text,
        re.S | re.I,
    )
    if not begin_alignment_match:
        begin_alignment_match = re.match(
            r"(?:\s|\\fontsize\s*\{[^{}]+\}\s*\{[^{}]+\}|\\selectfont\b|"
            r"\\normalfont\b|\\setstretch\s*\{[^{}]+\})*"
            r"\\(raggedright|centering|justifying)\b",
            document_prefix,
            re.S | re.I,
        )
    global_alignment = (
        begin_alignment_match.group(1).casefold()
        if begin_alignment_match
        else "justifying"
    )
    if global_alignment == "raggedright":
        body["alignment"] = "left"
    elif global_alignment == "centering":
        body["alignment"] = "center"
    else:
        body["alignment"] = "justify"
    if body:
        result["body"] = body

    detected_roles = []
    role_commands = {
        "udc": ("udc",),
        "title": ("title", "submissiontitle"),
        "authors": ("author", "authors", "submissionauthors"),
        "supervisor": ("supervisor",),
        "institution": ("institution", "organization"),
        "city_country": ("citycountry", "city"),
        "keywords": ("keywords",),
    }
    for role, names in role_commands.items():
        if any(name in commands or re.search(rf"\\{name}\b", text, re.I) for name in names):
            detected_roles.append(role)
    if re.search(r"\\begin\s*\{abstract\}", text, re.I):
        detected_roles.append("abstract")
    detected_roles.append("body")
    if re.search(
        r"\\begin\s*\{thebibliography\}|\\printbibliography\b|\\bibliography\s*\{",
        text,
        re.I,
    ):
        detected_roles.append("references")
    blocks = [
        {
            "role": role,
            "label": BLOCK_CATALOG[role]["label"],
            "required": role not in {"supervisor", "references"},
        }
        for role in dict.fromkeys(detected_roles)
    ]
    result["document"] = {"blocks": blocks}

    required_sections = [
        " ".join(value.split())
        for value in re.findall(
            r"\\section\*?(?:\[[^\]]*\])?\s*\{([^{}]+)\}",
            text,
            re.S | re.I,
        )
        if " ".join(value.split())
    ]
    if required_sections:
        result["structure"] = {"required_sections": required_sections}
    return normalize_template_rules(result)


def _issue(
    code: str,
    title: str,
    message: str,
    *,
    suggestion: str = "",
    fixable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "severity": "warning",
        "message": message,
        "location": "Оформление по LaTeX-шаблону",
        "context": "",
        "context_before": "",
        "context_highlight": "",
        "context_after": "",
        "suggestion": suggestion,
        "fixable": fixable,
    }


def _different(left: Any, right: Any, tolerance: float) -> bool:
    try:
        return abs(float(left) - float(right)) > tolerance
    except (TypeError, ValueError):
        return False


def check_latex_against_template(
    source: bytes | str,
    rules: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_template_rules(rules)
    actual = extract_latex_template_rules(source)
    raw = _strip_comments(_decode_source(source))
    plain_text = latex_to_plain_text(raw)
    commands = _command_values(raw)
    issues: list[dict[str, Any]] = []

    present = {
        "udc": bool(
            "udc" in commands
            or re.search(r"\\SubmissionUDC\b", raw)
            or re.search(r"\bУДК\b|\bUDC\b", plain_text, re.I)
        ),
        "title": bool("title" in commands or re.search(r"\\SubmissionTitle\b", raw)),
        "authors": bool(
            "author" in commands
            or "authors" in commands
            or re.search(r"\\SubmissionAuthors\b", raw)
        ),
        "supervisor": bool("supervisor" in commands or re.search(r"\\SubmissionSupervisor\b", raw)),
        "institution": bool(
            "institution" in commands
            or "organization" in commands
            or re.search(r"\\SubmissionInstitution\b", raw)
        ),
        "city_country": bool(
            "citycountry" in commands
            or "city" in commands
            or re.search(r"\\SubmissionCityCountry\b", raw)
        ),
        "abstract": bool(re.search(r"\\begin\s*\{abstract\}", raw, re.I)),
        "keywords": bool(
            "keywords" in commands
            or re.search(r"\\SubmissionKeywords\b", raw)
            or re.search(r"\bключевые\s+слова\b|\bkeywords\b", plain_text, re.I)
        ),
        "body": bool(_WORD_RE.search(plain_text)),
        "references": bool(
            re.search(
                r"\\begin\s*\{thebibliography\}|\\printbibliography\b|\\bibliography\s*\{",
                raw,
                re.I,
            )
        ),
    }
    blocks = []
    metadata = metadata or {}
    for block in get_document_blocks(normalized):
        role = block["role"]
        found = present.get(role, False)
        entry = {
            "role": role,
            "label": block.get("label") or BLOCK_CATALOG[role]["label"],
            "required": bool(block.get("required")),
            "found": found,
            "paragraph_numbers": [],
            "status": "found" if found else ("missing" if block.get("required") else "optional_missing"),
        }
        blocks.append(entry)
        if entry["required"] and not found:
            issues.append(
                _issue(
                    f"latex_missing_block_{role}",
                    f"Не найден блок «{entry['label']}»",
                    "В LaTeX-файле не найден обязательный элемент выбранного шаблона.",
                    suggestion="Добавьте соответствующую команду или блок из скачиваемого LaTeX-шаблона.",
                )
            )

    expected_page = normalized.get("page") or {}
    actual_page = actual.get("page") or {}
    expected_margins = expected_page.get("margins_cm") or {}
    actual_margins = actual_page.get("margins_cm") or {}
    for key, label in (
        ("top", "верхнее"),
        ("right", "правое"),
        ("bottom", "нижнее"),
        ("left", "левое"),
    ):
        expected = expected_margins.get(key)
        actual_value = actual_margins.get(key)
        if expected in (None, ""):
            continue
        if actual_value is None:
            issues.append(
                _issue(
                    f"latex_margin_{key}_not_declared",
                    f"Не задано {label} поле",
                    f"Шаблон требует {expected} см, но значение не найдено в geometry.",
                    suggestion="Укажите поле в параметрах пакета geometry.",
                )
            )
        elif _different(actual_value, expected, 0.08):
            issues.append(
                _issue(
                    f"latex_margin_{key}",
                    f"Отличается {label} поле",
                    f"В LaTeX-файле {actual_value} см, шаблон требует {expected} см.",
                    suggestion="Используйте параметры geometry из скачиваемого шаблона.",
                )
            )

    expected_body = normalized.get("body") or {}
    actual_body = actual.get("body") or {}
    for key, label, tolerance in (
        ("font_family", "основной шрифт", 0),
        ("font_size_pt", "размер шрифта", 0.2),
        ("line_spacing", "межстрочный интервал", 0.05),
        ("first_line_indent_cm", "абзацный отступ", 0.08),
        ("alignment", "выравнивание", 0),
    ):
        expected = expected_body.get(key)
        if expected in (None, ""):
            continue
        actual_value = actual_body.get(key)
        if actual_value in (None, ""):
            issues.append(
                _issue(
                    f"latex_{key}_not_declared",
                    f"Не задан: {label}",
                    f"В исходнике не найдено значение, требуемое шаблоном: {expected}.",
                    suggestion="Скопируйте настройку из скачиваемого LaTeX-шаблона.",
                )
            )
            continue
        differs = (
            str(actual_value).casefold() != str(expected).casefold()
            if key in {"font_family", "alignment"}
            else _different(actual_value, expected, tolerance)
        )
        if differs:
            issues.append(
                _issue(
                    f"latex_{key}",
                    f"Отличается: {label}",
                    f"В LaTeX-файле указано «{actual_value}», шаблон требует «{expected}».",
                    suggestion="Используйте настройку из скачиваемого LaTeX-шаблона.",
                )
            )

    actual_sections = {
        _normalized_heading(value)
        for value in re.findall(
            r"\\section\*?(?:\[[^\]]*\])?\s*\{([^{}]+)\}",
            raw,
            re.S | re.I,
        )
    }
    for section in (normalized.get("structure") or {}).get("required_sections") or []:
        if _normalized_heading(section) not in actual_sections:
            issues.append(
                _issue(
                    "latex_missing_section",
                    f"Нет раздела «{section}»",
                    "Обязательный раздел не найден среди команд section.",
                    suggestion="Добавьте раздел с помощью \\section{...}.",
                )
            )

    word_count = len(_WORD_RE.findall(plain_text))
    limits = normalized.get("limits") or {}
    if limits.get("min_words") and word_count < int(limits["min_words"]):
        issues.append(
            _issue(
                "latex_min_words",
                "Объём меньше минимального",
                f"Распознано {word_count} слов, требуется не менее {limits['min_words']}.",
                suggestion="Дополните научный текст самостоятельно.",
                fixable=False,
            )
        )
    if limits.get("max_words") and word_count > int(limits["max_words"]):
        issues.append(
            _issue(
                "latex_max_words",
                "Превышен максимальный объём",
                f"Распознано {word_count} слов, допускается не более {limits['max_words']}.",
                suggestion="Сократите текст без потери научного смысла.",
                fixable=False,
            )
        )

    title_values = commands.get("title") or []
    title_block = next(
        (item for item in get_document_blocks(normalized) if item["role"] == "title"),
        {},
    )
    if title_values and (title_block.get("constraints") or {}).get("uppercase"):
        letters = [character for character in title_values[0] if character.isalpha()]
        if letters and any(character.islower() for character in letters):
            issues.append(
                _issue(
                    "latex_title_uppercase",
                    "Название должно быть прописными буквами",
                    "Текст команды title не соответствует требованию шаблона.",
                    suggestion="Запишите название прописными буквами.",
                )
            )

    metrics = {
        "source_format": "latex",
        "word_count": word_count,
        "margins_cm": actual_margins,
        "font_family": actual_body.get("font_family"),
        "font_size_pt": actual_body.get("font_size_pt"),
        "line_spacing": actual_body.get("line_spacing"),
        "first_line_indent_cm": actual_body.get("first_line_indent_cm"),
        "alignment": actual_body.get("alignment"),
    }
    return {
        "schema_version": "2.0",
        "rules": normalized,
        "issues": issues,
        "metrics": metrics,
        "blocks": blocks,
        "role_assignments": [
            {"paragraph_number": 0, "role": role, "text": "LaTeX command"}
            for role, found in present.items()
            if found
        ],
        "can_build": True,
        "content_policy": normalized.get("document", {}).get("content_policy", {}),
    }


def _latex_escape(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    escaped = "".join(replacements.get(character, character) for character in text)
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\\ " )


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _macro_value(metadata: dict[str, Any], role: str, fallback: str) -> str:
    aliases = {
        "udc": ("udc",),
        "title": ("title",),
        "authors": ("authors", "document_authors"),
        "supervisor": ("supervisor",),
        "institution": ("institution", "organizations"),
        "city_country": ("city_country",),
        "abstract": ("abstract",),
        "keywords": ("keywords",),
    }
    for name in aliases.get(role, (role,)):
        value = str(metadata.get(name) or "").strip()
        if value:
            return _latex_escape(value)
    return fallback


def _alignment_environment(alignment: str) -> str:
    return {
        "center": "center",
        "центр": "center",
        "left": "flushleft",
        "по левому краю": "flushleft",
        "right": "flushright",
        "по правому краю": "flushright",
    }.get(str(alignment or "").casefold(), "")


def build_latex_template(
    rules: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> bytes:
    """Build a standalone UTF-8 XeLaTeX/LuaLaTeX template from normalized rules."""

    normalized = normalize_template_rules(rules)
    metadata = metadata or {}
    page = normalized.get("page") or {}
    margins = page.get("margins_cm") or {}
    body = normalized.get("body") or {}
    blocks = get_document_blocks(normalized)
    block_map = {item["role"]: item for item in blocks}
    block_order = list((normalized.get("document") or {}).get("block_order") or [])

    orientation = (
        ",landscape"
        if str(page.get("orientation") or "").casefold() in {"landscape", "альбомная"}
        else ""
    )
    geometry_options = [
        "a4paper" if str(page.get("size") or "A4").casefold() == "a4" else "",
        f"top={_number(margins.get('top'), 2):g}cm",
        f"right={_number(margins.get('right'), 2):g}cm",
        f"bottom={_number(margins.get('bottom'), 2):g}cm",
        f"left={_number(margins.get('left'), 2):g}cm",
    ]
    geometry_line = ",".join(item for item in geometry_options if item) + orientation
    font_family = str(body.get("font_family") or "Times New Roman").strip()
    font_size = _number(body.get("font_size_pt"), 14)
    line_spacing = _number(body.get("line_spacing"), 1)
    indent = _number(body.get("first_line_indent_cm"), 1)
    baseline = max(font_size * 1.2, font_size + 2)

    values = {
        "udc": _macro_value(metadata, "udc", "УДК 000.000"),
        "title": _macro_value(metadata, "title", "НАЗВАНИЕ НАУЧНОЙ РАБОТЫ"),
        "authors": _macro_value(metadata, "authors", "И.О. Фамилия автора"),
        "supervisor": _macro_value(metadata, "supervisor", "И.О. Фамилия, учёная степень"),
        "institution": _macro_value(metadata, "institution", "Название организации"),
        "city_country": _macro_value(metadata, "city_country", "Город, страна"),
        "abstract": _macro_value(metadata, "abstract", "Текст аннотации."),
        "keywords": _macro_value(metadata, "keywords", "ключевое слово; ключевое слово"),
    }
    macro_names = {
        "udc": "SubmissionUDC",
        "title": "SubmissionTitle",
        "authors": "SubmissionAuthors",
        "supervisor": "SubmissionSupervisor",
        "institution": "SubmissionInstitution",
        "city_country": "SubmissionCityCountry",
        "abstract": "SubmissionAbstract",
        "keywords": "SubmissionKeywords",
    }

    lines = [
        "% !TeX program = xelatex",
        "% Автоматически сформированный шаблон научного материала.",
        "% Компилируйте XeLaTeX или LuaLaTeX. Смысл и факты автор заполняет самостоятельно.",
        rf"\documentclass[12pt{orientation}]{{article}}",
        r"\usepackage{iftex}",
        r"\ifPDFTeX",
        r"  \PackageError{template}{Use XeLaTeX or LuaLaTeX}{Unicode fonts require XeLaTeX or LuaLaTeX.}",
        r"\fi",
        r"\usepackage{fontspec}",
        rf"\IfFontExistsTF{{{_latex_escape(font_family)}}}{{\setmainfont{{{_latex_escape(font_family)}}}}}{{\setmainfont{{TeX Gyre Termes}}}}",
        r"\usepackage{polyglossia}",
        r"\setdefaultlanguage{russian}",
        r"\setotherlanguage{english}",
        rf"\usepackage[{geometry_line}]{{geometry}}",
        r"\usepackage{setspace}",
        r"\usepackage{ragged2e}",
        r"\usepackage{enumitem}",
        r"\usepackage[hidelinks]{hyperref}",
        rf"\setlength{{\parindent}}{{{indent:g}cm}}",
        r"\setlength{\parskip}{0pt}",
        rf"\setstretch{{{line_spacing:g}}}",
        r"\sloppy",
        "",
    ]
    for role, macro in macro_names.items():
        lines.append(rf"\newcommand{{\{macro}}}{{{values[role]}}}")
    lines.extend(
        [
            r"\newcommand{\SubmissionBody}{%",
            r"  Замените этот текст собственным научным содержанием.%",
            r"}",
            "",
            r"\begin{document}",
            rf"\fontsize{{{font_size:g}pt}}{{{baseline:g}pt}}\selectfont",
        ]
    )
    if str(body.get("alignment") or "justify").casefold() in {"justify", "justified", "по ширине"}:
        lines.append(r"\justifying")
    lines.append("")

    for role in block_order:
        block = block_map.get(role, {})
        style = block.get("style") or {}
        environment = _alignment_environment(style.get("alignment"))
        prefix = [rf"\begin{{{environment}}}"] if environment else []
        suffix = [rf"\end{{{environment}}}"] if environment else []
        if role == "udc":
            content = [r"\noindent\SubmissionUDC\par"]
        elif role == "title":
            title_command = r"\MakeUppercase{\SubmissionTitle}" if (block.get("constraints") or {}).get("uppercase") else r"\SubmissionTitle"
            content = [rf"\textbf{{{title_command}}}\par"]
        elif role == "authors":
            content = [r"\SubmissionAuthors\par"]
        elif role == "supervisor":
            content = [r"\textit{Научный руководитель: \SubmissionSupervisor}\par"]
        elif role == "institution":
            content = [r"\SubmissionInstitution\par"]
        elif role == "city_country":
            content = [r"\SubmissionCityCountry\par"]
        elif role == "abstract":
            content = [
                r"\begin{abstract}",
                r"\SubmissionAbstract",
                r"\end{abstract}",
            ]
        elif role == "keywords":
            content = [r"\noindent\textbf{Ключевые слова:} \SubmissionKeywords\par"]
        elif role == "body":
            required_sections = (normalized.get("structure") or {}).get("required_sections") or []
            if required_sections:
                content = []
                for section in required_sections:
                    content.extend(
                        [
                            rf"\section{{{_latex_escape(section)}}}",
                            r"\SubmissionBody",
                            "",
                        ]
                    )
            else:
                content = [r"\SubmissionBody"]
        elif role == "references":
            content = [
                r"\begin{thebibliography}{9}",
                r"\bibitem{source1} Автор. Название источника. Год.",
                r"\end{thebibliography}",
            ]
        else:
            continue
        if not block.get("required"):
            lines.append(f"% Необязательный блок: {block.get('label') or BLOCK_CATALOG[role]['label']}")
        lines.extend([*prefix, *content, *suffix, ""])

    lines.extend([r"\end{document}", ""])
    return "\n".join(lines).encode("utf-8")
