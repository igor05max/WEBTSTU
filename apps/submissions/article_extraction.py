"""Format-independent semantic extraction for scientific documents.

The physical parsers in ``document_analysis`` produce ordered paragraph records.
This module classifies those records without depending on Django or a network
model.  Every extracted value carries confidence and source block identifiers so
that a local model or a user can review ambiguous decisions without inventing
content that is not present in the document.
"""

from __future__ import annotations

import json
import re
import statistics
import unicodedata
from typing import Any, Callable


SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’-]*")
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}(?![\w.-])",
    re.I,
)
AUTHOR_RE = re.compile(
    r"(?:[А-ЯЁA-Z]\s*\.\s*){1,3}[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+"
    r"|[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+\s+(?:[А-ЯЁA-Z]\s*\.\s*){1,3}"
)
FULL_NAME_RE = re.compile(
    r"\b[А-ЯЁ][А-ЯЁа-яё'’-]+\s+[А-ЯЁ][А-ЯЁа-яё'’-]+"
    r"(?:\s+[А-ЯЁ][А-ЯЁа-яё'’-]+)?\b"
)
LATIN_FULL_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z'’-]+\s+[A-Z][A-Za-z'’-]+"
    r"(?:\s+[A-Z][A-Za-z'’-]+)?\b"
)
NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+){0,3})[.)]?\s+(?P<title>[^\n]{2,140})$"
)
REFERENCE_ENTRY_RE = re.compile(
    r"^\s*(?:\[\s*\d+\s*\]|\d{1,3}[.)])\s+\S"
)
ABSTRACT_RE = re.compile(
    r"^\s*(?:аннотаци[яи]|abstract|резюме|summary)\s*(?:[:.–—-]\s*)?(?P<value>.*)$",
    re.I,
)
KEYWORDS_RE = re.compile(
    r"^\s*(?:ключев(?:ые|ыe)\s+слов[ао]|keywords?|key\s+words?)\s*"
    r"(?:[:.–—-]\s*)?(?P<value>.*)$",
    re.I,
)
REFERENCE_HEADINGS = {
    "список литературы",
    "список использованной литературы",
    "список использованных источников",
    "список использованных источников и литературы",
    "библиографический список",
    "библиография",
    "литература",
    "источники",
    "references",
    "reference list",
}
KNOWN_SECTION_HEADINGS = {
    "введение",
    "introduction",
    "обзор литературы",
    "literature review",
    "материалы и методы",
    "материал и методы",
    "методы",
    "методология",
    "materials and methods",
    "methods",
    "результаты",
    "results",
    "обсуждение",
    "discussion",
    "заключение",
    "выводы",
    "conclusion",
    "conclusions",
    *REFERENCE_HEADINGS,
}
ORGANIZATION_MARKERS = (
    "университет",
    "институт",
    "академ",
    "колледж",
    "кафедр",
    "лаборатор",
    "факультет",
    "центр",
    "фгбоу",
    "фгаоу",
    "university",
    "institute",
    "academy",
    "department",
    "laboratory",
    "research center",
)
SUPERVISOR_MARKERS = (
    "научный руководитель",
    "руководитель",
    "scientific supervisor",
    "research supervisor",
)
FRONT_LABEL_MARKERS = (
    "удк",
    "doi",
    "orcid",
    "edn",
    "аннотац",
    "abstract",
    "ключев",
    "keyword",
)


def normalize_space(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "").replace("\x00", " ")).strip()


def normalize_for_match(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = normalize_space(text).casefold().replace("ё", "е")
    return text.strip(" \t\r\n.:;–—-")


def _uppercase_ratio(value: str) -> float:
    letters = [character for character in value if character.isalpha()]
    if not letters:
        return 0.0
    return sum(character.isupper() for character in letters) / len(letters)


def _field(
    value: Any,
    *,
    confidence: float,
    block_ids: list[str] | None = None,
    method: str,
    alternatives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "value": value,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "source_block_ids": list(block_ids or []),
        "method": method,
    }
    if alternatives:
        result["alternatives"] = alternatives
    return result


def _record_id(record: dict[str, Any], order: int) -> str:
    return str(record.get("block_id") or f"p:{record.get('index', order)}")


def _style_text(record: dict[str, Any]) -> str:
    return normalize_for_match(record.get("style") or "")


def _is_heading_style(record: dict[str, Any]) -> bool:
    style = _style_text(record)
    text = normalize_space(record.get("text"))
    has_heading_style = "heading" in style or "заголов" in style
    has_outline = record.get("outline_level") is not None
    return bool(
        (has_heading_style or has_outline)
        and WORD_RE.search(text)
        and len(WORD_RE.findall(text)) <= 18
        and not re.search(r"[{};=±∑∫√\\_]", text)
        and not re.fullmatch(r"\(?\s*\d{1,3}\s*\)?", text)
        and not text.endswith((".", ":", ";"))
        and not re.match(
            r"^\s*(?:листинг|listing|рис(?:унок)?\.?|figure|табл(?:ица)?\.?|table)\s*\d+",
            text,
            re.I,
        )
    )


def _has_semantic_style(record: dict[str, Any], role: str) -> bool:
    style = _style_text(record).replace(" ", "_")
    role_markers = {
        "title": ("_title", "title_", "latex:title"),
        "authors": ("authorname", "_authors", "latex:authors"),
        "institution": ("affiliation", "institution", "latex:institution"),
        "abstract": ("_abstract", "abstract_", "latex:abstract"),
        "keywords": ("_keyword", "keyword_", "latex:keywords"),
    }
    return any(marker in style for marker in role_markers.get(role, ()))


def _is_centered(record: dict[str, Any]) -> bool:
    return str(record.get("alignment") or "").casefold() in {"center", "centered"}


def _is_bold(record: dict[str, Any]) -> bool:
    return record.get("bold") is True


def _body_font_size(paragraphs: list[dict[str, Any]]) -> float | None:
    values = [
        float(record["font_size_pt"])
        for record in paragraphs
        if record.get("font_size_pt") not in (None, "")
        and len(record.get("text") or "") >= 80
    ]
    return statistics.median(values) if values else None


def _title_candidates(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    body_size = _body_font_size(paragraphs)
    udc_orders = [
        order
        for order, record in enumerate(paragraphs[:30])
        if normalize_for_match(record.get("text")).startswith(("удк", "udc"))
    ]
    first_udc = udc_orders[0] if udc_orders else None
    candidates = []
    for order, record in enumerate(paragraphs[:40]):
        text = normalize_space(record.get("text"))
        normalized = normalize_for_match(text)
        if record.get("container") == "table_cell":
            continue
        if not (6 <= len(text) <= 320):
            continue
        if normalized in KNOWN_SECTION_HEADINGS:
            continue
        if ABSTRACT_RE.match(text) or KEYWORDS_RE.match(text):
            continue
        if re.match(
            r"^\s*(?:рис(?:унок)?\.?|figure|табл(?:ица)?\.?|table)\s*\d+",
            text,
            re.I,
        ):
            continue
        if any(normalized.startswith(marker) for marker in FRONT_LABEL_MARKERS):
            continue
        if "@" in text or REFERENCE_ENTRY_RE.match(text):
            continue
        if any(marker in normalized for marker in SUPERVISOR_MARKERS):
            continue
        if (
            any(marker in normalized for marker in ORGANIZATION_MARKERS)
            and order > 0
            and (_uppercase_ratio(text) < 0.70 or len(text) < 55)
        ):
            continue
        if re.fullmatch(
            r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+(?:\s*,\s*|\s+)"
            r"(?:Россия|Беларусь|Казахстан|Russia|Belarus|Kazakhstan)",
            text,
            re.I,
        ):
            continue
        if AUTHOR_RE.fullmatch(text) or FULL_NAME_RE.fullmatch(text):
            continue

        score = 0.10
        signals = []
        position_bonus = max(0.0, 0.20 - order * 0.008)
        score += position_bonus
        signals.append("early_position")
        if order == 0:
            score += 0.13
            signals.append("first_block")
        if first_udc is not None and first_udc < order <= first_udc + 4:
            score += 0.18
            signals.append("after_udc")
        if _uppercase_ratio(text) >= 0.72 and len(text) >= 18:
            score += 0.18
            signals.append("uppercase")
        if _is_centered(record):
            score += 0.14
            signals.append("centered")
        if _is_bold(record):
            score += 0.12
            signals.append("bold")
        if _is_heading_style(record):
            score += 0.12
            signals.append("heading_style")
        if _has_semantic_style(record, "title"):
            score += 0.46
            signals.append("semantic_title_style")
        next_labels = {
            normalize_for_match(item.get("text"))
            for item in paragraphs[order + 1 : order + 3]
        }
        if next_labels & {"автор", "авторы", "author", "authors"}:
            score += 0.26
            signals.append("followed_by_authors_label")
        size = record.get("font_size_pt")
        if size and body_size and float(size) >= body_size + 2:
            score += 0.15
            signals.append("larger_font")
        elif size and float(size) >= 16:
            score += 0.10
            signals.append("large_font")
        if 18 <= len(text) <= 180:
            score += 0.07
        if text.endswith((".", ";")) and _uppercase_ratio(text) < 0.65:
            score -= 0.12
        if len(re.findall(r"[.!?](?:\s|$)", text)) >= 2:
            score -= 0.20
        candidates.append(
            {
                "order": order,
                "record": record,
                "value": text,
                "score": max(0.0, min(1.0, score)),
                "signals": signals,
            }
        )
    return sorted(candidates, key=lambda item: (item["score"], -item["order"]), reverse=True)


def _split_embedded_section_heading(text: str) -> tuple[str, str]:
    """Split a PDF block where a short heading was merged with its first sentence."""

    match = re.match(
        r"^(?P<number>\d+(?:\.\d+){0,3}[.)]?)\s+"
        r"(?P<title>(?:[A-Z][A-Za-z'’-]*\s+){1,8}?)"
        r"(?P<body>(?:The|This|These|In|A|An|We)\s+.+)$",
        normalize_space(text),
    )
    if not match:
        return "", ""
    title = normalize_space(f"{match.group('number')} {match.group('title')}")
    return title, normalize_space(match.group("body"))


def _looks_like_section(record: dict[str, Any], *, allow_styled=True) -> tuple[bool, int]:
    text = normalize_space(record.get("text"))
    normalized = normalize_for_match(text)
    if normalized in KNOWN_SECTION_HEADINGS:
        return True, 1
    embedded_title, _embedded_body = _split_embedded_section_heading(text)
    if embedded_title:
        number = embedded_title.split(maxsplit=1)[0].rstrip(".)")
        return True, min(4, number.count(".") + 1)
    numbered = NUMBERED_HEADING_RE.match(text)
    numbered_title = numbered.group("title").strip() if numbered else ""
    if (
        numbered
        and not text.endswith(("!", "?", ":", ";"))
        and numbered_title[:1].isupper()
        and not re.search(r"[&=±∑\\_]", text)
        and not re.fullmatch(
            r"[A-Za-zА-ЯЁа-яё]\s*[-+/*]\s*\d+",
            numbered_title.strip(" ."),
        )
        and not re.search(r"\(\s*\d{1,3}\s*\)\s*$", text)
        and len(text) <= 170
        and numbered_title.count(". ") <= 1
        and text.count(",") < 2
        and len(WORD_RE.findall(text)) <= 18
    ):
        level = min(4, numbered.group("number").count(".") + 1)
        return True, level
    if not allow_styled or len(text) > 170 or len(WORD_RE.findall(text)) > 16:
        return False, 0
    if re.match(r"^\s*(?:рис(?:унок)?\.?|figure|табл(?:ица)?\.?|table)\s*\d+", text, re.I):
        return False, 0
    if _is_heading_style(record):
        level_match = re.search(r"(?:heading|заголовок)\s*(\d+)", _style_text(record))
        return True, int(level_match.group(1)) if level_match else 1
    if (
        _is_bold(record)
        and not text.endswith((".", ";", "?", "!", ":"))
        and len(WORD_RE.findall(text)) <= 10
        and not re.search(r"[{}=±∑∫√\\_]", text)
        and not re.fullmatch(r"\(?\s*\d{1,3}\s*\)?", text)
        and not re.match(r"^\s*(?:RQ|Вопрос)\s*\d+", text, re.I)
    ):
        return True, 1
    return False, 0


def _front_matter_end(
    paragraphs: list[dict[str, Any]],
    title_order: int,
) -> int:
    start = max(0, title_order + 1)
    for order in range(start, min(len(paragraphs), start + 30)):
        record = paragraphs[order]
        text = normalize_space(record.get("text"))
        normalized = normalize_for_match(text)
        if ABSTRACT_RE.match(text) or KEYWORDS_RE.match(text):
            continue
        if (
            AUTHOR_RE.search(text)
            or FULL_NAME_RE.fullmatch(text)
            or any(marker in normalized for marker in SUPERVISOR_MARKERS)
            or any(marker in normalized for marker in ORGANIZATION_MARKERS)
            or re.fullmatch(
                r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+(?:\s*,\s*|\s+)"
                r"(?:Россия|Беларусь|Казахстан|Russia|Belarus|Kazakhstan)",
                text,
                re.I,
            )
        ):
            continue
        is_section, _level = _looks_like_section(record)
        if is_section:
            return order
        if order <= title_order + 2:
            continue
        word_count = len(WORD_RE.findall(text))
        if len(text) >= 140 and word_count >= 18:
            if str(record.get("alignment") or "").casefold() == "justify" or text.endswith(
                (".", "!", "?")
            ):
                return order
    return min(len(paragraphs), start + 16)


def _author_surname(value: str) -> str:
    tokens = re.findall(r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+", value)
    non_initials = [token for token in tokens if len(token) > 1]
    if not non_initials:
        return normalize_for_match(value)

    uppercase = [
        token
        for token in non_initials
        if len(token) > 1 and _uppercase_ratio(token) >= 0.9
    ]
    if len(uppercase) == 1:
        return normalize_for_match(uppercase[0])

    cyrillic = [
        token
        for token in non_initials
        if re.search(r"[А-ЯЁа-яё]", token)
    ]
    if len(cyrillic) >= 3:
        normalized = [normalize_for_match(token) for token in cyrillic]
        patronymic_suffixes = (
            "вич",
            "вна",
            "ична",
            "инична",
            "овна",
            "евна",
            "оглы",
            "кызы",
        )
        if normalized[-1].endswith(patronymic_suffixes):
            return normalized[0]
        if normalized[-2].endswith(patronymic_suffixes):
            return normalized[-1]

    return normalize_for_match(non_initials[-1])


def _extract_authors(
    paragraphs: list[dict[str, Any]],
    *,
    title_order: int,
    front_end: int,
) -> tuple[list[str], list[str], float]:
    values: list[str] = []
    block_ids: list[str] = []
    seen: set[str] = set()
    author_label_seen = False
    for order in range(max(0, title_order + 1), min(len(paragraphs), front_end)):
        record = paragraphs[order]
        text = normalize_space(record.get("text"))
        normalized = normalize_for_match(text)
        if normalized in {"автор", "авторы", "author", "authors"}:
            author_label_seen = True
            continue
        if (
            not text
            or "@" in text
            or len(text) > 320
            or any(marker in normalized for marker in SUPERVISOR_MARKERS)
            or any(marker in normalized for marker in ORGANIZATION_MARKERS)
            or ABSTRACT_RE.match(text)
            or KEYWORDS_RE.match(text)
        ):
            continue
        matches = [normalize_space(match.group(0)) for match in AUTHOR_RE.finditer(text)]
        if not matches and len(text) <= 140:
            matches = [normalize_space(match.group(0)) for match in FULL_NAME_RE.finditer(text)]
        if (
            not matches
            and len(text) <= 500
            and (
                author_label_seen
                or _has_semantic_style(record, "authors")
                or LATIN_FULL_NAME_RE.search(text)
            )
        ):
            cleaned = re.sub(r"\\[A-Za-z@]+\*?(?:\{[^{}]*\})?", "", text)
            cleaned = re.sub(r"\[[0-9Xx-]{8,}\]", "", cleaned)
            cleaned = re.sub(r"\b\d{4}-\d{4}-\d{4}-[\dXx]{4}\b", "", cleaned)
            for chunk in re.split(r"\s*(?:,|;|\band\b|\bи\b)\s*", cleaned, flags=re.I):
                candidate = normalize_space(chunk.strip(" *0123456789,;"))
                latin_match = LATIN_FULL_NAME_RE.fullmatch(candidate)
                cyrillic_match = FULL_NAME_RE.fullmatch(candidate)
                if latin_match or cyrillic_match:
                    matches.append(candidate)
        found_in_record = False
        for candidate in matches:
            surname = _author_surname(candidate)
            if not surname or surname in seen:
                continue
            seen.add(surname)
            values.append(candidate)
            found_in_record = True
        if found_in_record:
            block_ids.append(_record_id(record, order))
            author_label_seen = True
    confidence = 0.88 if values and title_order >= 0 else 0.68 if values else 0.0
    return values[:50], block_ids, confidence


def _collect_labeled_value(
    paragraphs: list[dict[str, Any]],
    pattern: re.Pattern[str],
) -> tuple[str, list[str], float]:
    for order, record in enumerate(paragraphs):
        text = normalize_space(record.get("text"))
        match = pattern.match(text)
        if not match:
            continue
        block_ids = [_record_id(record, order)]
        value = normalize_space(match.group("value"))
        if value:
            if pattern is KEYWORDS_RE:
                for next_order in range(order + 1, min(len(paragraphs), order + 3)):
                    next_record = paragraphs[next_order]
                    next_text = normalize_space(next_record.get("text"))
                    same_page = (
                        record.get("page") is None
                        or next_record.get("page") == record.get("page")
                    )
                    if (
                        same_page
                        and next_text[:1].islower()
                        and not _looks_like_section(next_record)[0]
                        and not re.search(r"[{}]", next_text)
                        and len(next_text) <= 500
                    ):
                        value = normalize_space(
                            value[:-1] + next_text
                            if value.endswith(("-", "‑"))
                            else f"{value} {next_text}"
                        )
                        block_ids.append(_record_id(next_record, next_order))
                        continue
                    break
            return value, block_ids, 0.98
        chunks = []
        for next_order in range(order + 1, min(len(paragraphs), order + 6)):
            next_record = paragraphs[next_order]
            next_text = normalize_space(next_record.get("text"))
            if (
                ABSTRACT_RE.match(next_text)
                or KEYWORDS_RE.match(next_text)
                or _looks_like_section(next_record)[0]
            ):
                break
            if next_text:
                chunks.append(next_text)
                block_ids.append(_record_id(next_record, next_order))
            if pattern is KEYWORDS_RE or len(" ".join(chunks)) >= 2500:
                break
        return normalize_space(" ".join(chunks)), block_ids, 0.90 if chunks else 0.25
    return "", [], 0.0


def _split_keywords(value: str) -> list[str]:
    if not value:
        return []
    chunks = re.split(r"\s*(?:[,;•]|\s+[–—]\s+)\s*", value)
    return [chunk.strip(" .") for chunk in chunks if chunk.strip(" .")]


def _extract_organizations(
    paragraphs: list[dict[str, Any]],
    *,
    title_order: int,
    front_end: int,
) -> tuple[list[str], list[str], float]:
    organizations = []
    block_ids = []
    seen = set()
    for order in range(max(0, title_order + 1), min(len(paragraphs), front_end)):
        record = paragraphs[order]
        text = normalize_space(record.get("text"))
        normalized = normalize_for_match(text)
        if not text or len(text) > 320:
            continue
        if any(marker in normalized for marker in SUPERVISOR_MARKERS):
            continue
        if not any(marker in normalized for marker in ORGANIZATION_MARKERS):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        organizations.append(text)
        block_ids.append(_record_id(record, order))
    return organizations, block_ids, 0.88 if organizations else 0.0


def _extract_sections(
    paragraphs: list[dict[str, Any]],
    *,
    body_start: int,
) -> tuple[list[dict[str, Any]], int | None]:
    headings = []
    reference_order = None
    for order in range(max(0, body_start), len(paragraphs)):
        record = paragraphs[order]
        if record.get("container") == "table_cell":
            continue
        is_heading, level = _looks_like_section(record)
        if not is_heading:
            continue
        title = normalize_space(record.get("text"))
        embedded_title, embedded_body = _split_embedded_section_heading(title)
        if embedded_title:
            title = embedded_title
        normalized = normalize_for_match(title)
        kind = "references" if normalized in REFERENCE_HEADINGS else "section"
        if kind == "references" and reference_order is None:
            reference_order = order
        headings.append(
            {
                "order": order,
                "title": title,
                "level": level,
                "kind": kind,
                "confidence": 0.98
                if normalized in KNOWN_SECTION_HEADINGS
                else 0.90
                if _is_heading_style(record)
                else 0.72,
                "source_block_id": _record_id(record, order),
                "leading_content": embedded_body,
            }
        )
        if kind == "references":
            break

    sections = []
    for index, heading in enumerate(headings):
        end = headings[index + 1]["order"] if index + 1 < len(headings) else len(paragraphs)
        content_records = paragraphs[heading["order"] + 1 : end]
        content_lines = [
            normalize_space(heading.get("leading_content")),
            *[
                normalize_space(record.get("text"))
                for record in content_records
                if normalize_space(record.get("text"))
            ],
        ]
        sections.append(
            {
                key: value
                for key, value in heading.items()
                if key not in {"order", "leading_content"}
            }
            | {
                "content": "\n".join(line for line in content_lines if line),
                "content_block_ids": (
                    [heading["source_block_id"]]
                    if heading.get("leading_content")
                    else []
                )
                + [
                    _record_id(record, order)
                    for order, record in enumerate(
                        content_records,
                        start=heading["order"] + 1,
                    )
                    if normalize_space(record.get("text"))
                ],
            }
        )
    return sections, reference_order


def _extract_references(
    paragraphs: list[dict[str, Any]],
    reference_order: int | None,
) -> tuple[list[dict[str, Any]], float]:
    start = reference_order + 1 if reference_order is not None else None
    confidence = 0.98 if start is not None else 0.0
    if start is None:
        candidates = [
            order
            for order, record in enumerate(paragraphs)
            if order >= int(len(paragraphs) * 0.65)
            and REFERENCE_ENTRY_RE.match(normalize_space(record.get("text")))
        ]
        if len(candidates) >= 2:
            start = candidates[0]
            confidence = 0.62
    if start is None:
        return [], 0.0

    numbered_mode = any(
        REFERENCE_ENTRY_RE.match(normalize_space(record.get("text")))
        for record in paragraphs[start:]
    )
    references = []
    current: dict[str, Any] | None = None
    for order in range(start, len(paragraphs)):
        record = paragraphs[order]
        text = normalize_space(record.get("text"))
        if not text:
            continue
        embedded_reference = re.search(
            r"(?<![\d.])(?:\[\s*\d+\s*\]|\d{1,3}[.)])\s+"
            r"[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё'’-]+,\s*[A-ZА-ЯЁ]",
            text,
        )
        if embedded_reference and embedded_reference.start() > 0:
            text = text[embedded_reference.start() :]
        text = re.split(
            r"\b(?:Disclaimer/Publisher['’]s Note|Отказ от ответственности издателя)\s*:",
            text,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        numbered = REFERENCE_ENTRY_RE.match(text)
        if numbered or current is None or not numbered_mode:
            if current:
                references.append(current)
            number_match = re.match(r"^\s*(?:\[\s*(\d+)\s*\]|(\d+)[.)])", text)
            current = {
                "text": text,
                "number": int(next(value for value in number_match.groups() if value))
                if number_match
                else None,
                "source_block_ids": [_record_id(record, order)],
                "confidence": confidence,
            }
        else:
            current["text"] = normalize_space(f"{current['text']} {text}")
            current["source_block_ids"].append(_record_id(record, order))
    if current:
        references.append(current)
    return references, confidence


def extract_article_structure(snapshot: dict[str, Any]) -> dict[str, Any]:
    paragraphs = [
        record
        for record in (snapshot.get("paragraphs") or [])
        if normalize_space(record.get("text"))
        and str(record.get("region") or "document") == "document"
        and record.get("container") != "table_cell"
    ]
    candidates = _title_candidates(paragraphs)
    best_title = candidates[0] if candidates else None
    title_order = best_title["order"] if best_title else -1
    title_value = best_title["value"] if best_title else ""
    title_confidence = best_title["score"] if best_title else 0.0
    front_end = _front_matter_end(paragraphs, title_order)

    abstract, abstract_blocks, abstract_confidence = _collect_labeled_value(
        paragraphs,
        ABSTRACT_RE,
    )
    keywords_text, keyword_blocks, keyword_confidence = _collect_labeled_value(
        paragraphs,
        KEYWORDS_RE,
    )
    order_by_block_id = {
        _record_id(record, order): order
        for order, record in enumerate(paragraphs)
    }
    labeled_orders = [
        order_by_block_id[block_id]
        for block_id in [*abstract_blocks, *keyword_blocks]
        if block_id in order_by_block_id
    ]
    if labeled_orders:
        front_end = max(front_end, max(labeled_orders) + 1)

    authors, author_blocks, author_confidence = _extract_authors(
        paragraphs,
        title_order=title_order,
        front_end=front_end,
    )
    organizations, organization_blocks, organization_confidence = _extract_organizations(
        paragraphs,
        title_order=title_order,
        front_end=front_end,
    )
    keywords = _split_keywords(keywords_text)

    sections, reference_order = _extract_sections(paragraphs, body_start=front_end)
    references, references_confidence = _extract_references(paragraphs, reference_order)
    body_end = reference_order if reference_order is not None else len(paragraphs)
    heading_ids = {section["source_block_id"] for section in sections}
    body_records = [
        (order, record)
        for order, record in enumerate(paragraphs[front_end:body_end], start=front_end)
        if _record_id(record, order) not in heading_ids
    ]
    body_text = "\n".join(
        normalize_space(record.get("text"))
        for _order, record in body_records
        if normalize_space(record.get("text"))
    )
    emails = []
    seen_emails = set()
    email_start = max(0, title_order + 1)
    email_records = paragraphs[email_start : max(front_end, email_start)]
    front_text = "\n".join(
        normalize_space(record.get("text")) for record in email_records
    )
    if not EMAIL_RE.search(front_text):
        email_start = 0
        email_records = paragraphs[: max(front_end, title_order + 1)]
        front_text = "\n".join(
            normalize_space(record.get("text")) for record in email_records
        )
    for email in EMAIL_RE.findall(front_text):
        lowered = email.casefold()
        if lowered not in seen_emails:
            seen_emails.add(lowered)
            emails.append(email)

    alternative_titles = [
        {
            "value": candidate["value"],
            "confidence": round(candidate["score"], 3),
            "source_block_ids": [
                _record_id(candidate["record"], candidate["order"])
            ],
        }
        for candidate in candidates[1:4]
        if candidate["score"] >= max(0.35, title_confidence - 0.22)
    ]
    title_blocks = (
        [_record_id(best_title["record"], best_title["order"])]
        if best_title
        else []
    )
    scientific_signals = bool(
        authors
        or abstract
        or keywords
        or references
        or any(
            normalize_for_match(record.get("text")).startswith(("удк", "udc"))
            for record in paragraphs[:20]
        )
    )
    needs_review = []
    if paragraphs and not title_value:
        needs_review.append(
            {
                "field": "title",
                "reason": "Название не распознано среди абзацев документа.",
                "candidate_block_ids": [
                    _record_id(record, order)
                    for order, record in enumerate(paragraphs[:20])
                ],
            }
        )
    elif title_value and title_confidence < 0.68:
        needs_review.append(
            {
                "field": "title",
                "reason": "Название определено неоднозначно.",
                "candidate_block_ids": title_blocks
                + [
                    item["source_block_ids"][0]
                    for item in alternative_titles
                ],
            }
        )
    if scientific_signals and not authors:
        needs_review.append(
            {
                "field": "authors",
                "reason": "Авторы не распознаны в титульной части.",
                "candidate_block_ids": [
                    _record_id(record, order)
                    for order, record in enumerate(
                        paragraphs[max(0, title_order + 1) : front_end],
                        start=max(0, title_order + 1),
                    )
                ],
            }
        )
    if len(paragraphs) >= 4 and not abstract:
        needs_review.append(
            {
                "field": "abstract",
                "reason": "Аннотация не распознана или не имеет явной метки.",
                "candidate_block_ids": [
                    _record_id(record, order)
                    for order, record in enumerate(paragraphs[:30])
                ],
            }
        )
    if len(paragraphs) >= 4 and not keywords:
        needs_review.append(
            {
                "field": "keywords",
                "reason": "Ключевые слова не распознаны или не имеют явной метки.",
                "candidate_block_ids": [
                    _record_id(record, order)
                    for order, record in enumerate(paragraphs[:35])
                ],
            }
        )
    if len(paragraphs) >= 8 and not sections:
        needs_review.append(
            {
                "field": "sections",
                "reason": "Заголовки разделов не распознаны по оформлению.",
                "candidate_block_ids": [
                    _record_id(record, order)
                    for order, record in enumerate(paragraphs)
                    if len(WORD_RE.findall(record.get("text") or "")) <= 18
                ][:150],
            }
        )
    if snapshot.get("requires_ocr"):
        needs_review.append(
            {
                "field": "document",
                "reason": "В файле нет извлекаемого текстового слоя; требуется OCR.",
                "candidate_block_ids": [],
            }
        )

    return {
        "schema_version": "1.0",
        "source_format": snapshot.get("suffix", "").lstrip("."),
        "title": _field(
            title_value,
            confidence=title_confidence,
            block_ids=title_blocks,
            method="layout_and_text_heuristics",
            alternatives=alternative_titles,
        ),
        "authors": _field(
            authors,
            confidence=author_confidence,
            block_ids=author_blocks,
            method="front_matter_name_patterns",
        ),
        "organizations": _field(
            organizations,
            confidence=organization_confidence,
            block_ids=organization_blocks,
            method="front_matter_organization_markers",
        ),
        "emails": _field(
            emails,
            confidence=0.98 if emails else 0.0,
            block_ids=[
                _record_id(record, order)
                for order, record in enumerate(
                    email_records,
                    start=email_start,
                )
                if EMAIL_RE.search(record.get("text") or "")
            ],
            method="validated_pattern",
        ),
        "abstract": _field(
            abstract,
            confidence=abstract_confidence,
            block_ids=abstract_blocks,
            method="explicit_label",
        ),
        "keywords": _field(
            keywords,
            confidence=keyword_confidence,
            block_ids=keyword_blocks,
            method="explicit_label",
        ),
        "body": _field(
            body_text,
            confidence=0.86 if body_text else 0.0,
            block_ids=[_record_id(record, order) for order, record in body_records],
            method="front_matter_and_section_boundaries",
        ),
        "sections": sections,
        "references": references,
        "references_confidence": round(references_confidence, 3),
        "tables": snapshot.get("tables") or [],
        "figures": snapshot.get("figures") or [],
        "formulas": snapshot.get("formulas") or [],
        "needs_review": needs_review,
    }


def article_to_legacy_metadata(article: dict[str, Any]) -> dict[str, Any]:
    def value(name: str, default: Any = "") -> Any:
        field = article.get(name) or {}
        return field.get("value", default) if isinstance(field, dict) else default

    authors = list(value("authors", []) or [])
    organizations = list(value("organizations", []) or [])
    emails = list(value("emails", []) or [])
    keywords = list(value("keywords", []) or [])
    return {
        "title": str(value("title") or ""),
        "authors": authors,
        "document_authors": "\n".join(authors),
        "organizations": "\n".join(organizations),
        "emails": emails,
        "contact_emails": ", ".join(emails),
        "abstract": str(value("abstract") or ""),
        "keywords": ", ".join(keywords),
        "confidence": {
            key: (article.get(key) or {}).get("confidence", 0)
            for key in (
                "title",
                "authors",
                "organizations",
                "emails",
                "abstract",
                "keywords",
                "body",
            )
        },
        "needs_review": article.get("needs_review") or [],
    }


def build_semantic_review_prompt(
    snapshot: dict[str, Any],
    article: dict[str, Any],
) -> str:
    """Build a grounded prompt for a local model.

    The model may only select supplied block IDs.  Its response can therefore be
    validated before it affects user-visible metadata.
    """

    blocks = []
    character_budget = 80_000
    for order, record in enumerate((snapshot.get("paragraphs") or [])[:1200]):
        text = normalize_space(record.get("text"))
        if (
            not text
            or str(record.get("region") or "document") != "document"
            or character_budget <= 0
        ):
            continue
        text = text[: min(2000, character_budget)]
        character_budget -= len(text)
        blocks.append(
            {
                "order": order,
                "id": _record_id(record, order),
                "text": text,
                "style": record.get("style") or "",
                "alignment": record.get("alignment") or "",
                "bold": record.get("bold"),
                "font_size_pt": record.get("font_size_pt"),
                "outline_level": record.get("outline_level"),
                "page": record.get("page"),
            }
        )
    current_result = {
        name: article.get(name)
        for name in (
            "title",
            "authors",
            "organizations",
            "abstract",
            "keywords",
        )
    }
    current_result["sections"] = [
        {
            "title": section.get("title"),
            "level": section.get("level"),
            "source_block_id": section.get("source_block_id"),
            "confidence": section.get("confidence"),
        }
        for section in (article.get("sections") or [])
    ]
    current_result["needs_review"] = article.get("needs_review") or []
    request = {
        "title": {"block_ids": [], "confidence": 0},
        "authors": {"block_ids": [], "confidence": 0},
        "organizations": {"block_ids": [], "confidence": 0},
        "abstract": {"block_ids": [], "confidence": 0},
        "keywords": {"block_ids": [], "confidence": 0},
        "section_headings": [
            {"block_id": "", "level": 1, "confidence": 0}
        ],
    }
    return (
        "Определи структуру научного документа с неизвестным и возможным плохим "
        "оформлением. Используй только переданные блоки. Не исправляй и не дополняй "
        "текст. Для каждого поля верни только ID исходных блоков и уверенность 0..1. "
        "Документ может быть на любом языке, но все служебные пояснения всегда пиши "
        "по-русски. "
        "Если данных нет, верни пустой список. Ответ — один JSON без Markdown.\n"
        f"Схема ответа: {json.dumps(request, ensure_ascii=False)}\n"
        "Текущий детерминированный результат: "
        f"{json.dumps(current_result, ensure_ascii=False)}\n"
        f"Блоки: {json.dumps(blocks, ensure_ascii=False)}"
    )


def refine_article_with_model(
    snapshot: dict[str, Any],
    article: dict[str, Any],
    *,
    complete_json: Callable[[str], str],
) -> dict[str, Any]:
    """Apply only grounded, higher-confidence model selections.

    This intentionally does not let the model return field text.  Values are
    reconstructed from source blocks, which prevents hallucinated names, titles,
    affiliations, and requirements.
    """

    raw = str(complete_json(build_semantic_review_prompt(snapshot, article)) or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return article
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return article
    if not isinstance(payload, dict):
        return article

    records = {
        _record_id(record, order): record
        for order, record in enumerate(snapshot.get("paragraphs") or [])
    }
    result = json.loads(json.dumps(article, ensure_ascii=False))
    for name in ("title", "authors", "organizations", "abstract", "keywords"):
        proposal = payload.get(name)
        if not isinstance(proposal, dict):
            continue
        ids = [
            str(block_id)
            for block_id in (proposal.get("block_ids") or [])
            if str(block_id) in records
        ]
        try:
            confidence = float(proposal.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        current = result.get(name) or {}
        if not ids or confidence <= float(current.get("confidence") or 0) + 0.05:
            continue
        texts = [normalize_space(records[block_id].get("text")) for block_id in ids]
        if name == "authors":
            values = []
            for text in texts:
                text_values = [
                    normalize_space(match.group(0))
                    for match in AUTHOR_RE.finditer(text)
                ]
                if not text_values:
                    text_values = [
                        normalize_space(match.group(0))
                        for match in FULL_NAME_RE.finditer(text)
                    ]
                if not text_values:
                    text_values = [
                        normalize_space(match.group(0))
                        for match in LATIN_FULL_NAME_RE.finditer(text)
                    ]
                values.extend(text_values)
            selected_value: Any = list(dict.fromkeys(values))
        elif name == "organizations":
            selected_value = texts
        elif name == "keywords":
            selected_value = _split_keywords(
                " ".join(
                    (KEYWORDS_RE.match(text).group("value") if KEYWORDS_RE.match(text) else text)
                    for text in texts
                )
            )
        elif name == "abstract":
            selected_value = " ".join(
                (ABSTRACT_RE.match(text).group("value") if ABSTRACT_RE.match(text) else text)
                for text in texts
            ).strip()
        else:
            selected_value = " ".join(texts).strip()
        if selected_value:
            result[name] = _field(
                selected_value,
                confidence=confidence,
                block_ids=ids,
                method="grounded_local_model",
            )
    section_proposals = payload.get("section_headings")
    if isinstance(section_proposals, list):
        selected_headings = []
        seen_ids = set()
        order_by_id = {
            block_id: order
            for order, block_id in enumerate(records)
        }
        for proposal in section_proposals:
            if not isinstance(proposal, dict):
                continue
            block_id = str(proposal.get("block_id") or "")
            if block_id not in records or block_id in seen_ids:
                continue
            try:
                confidence = float(proposal.get("confidence") or 0)
                level = max(1, min(4, int(proposal.get("level") or 1)))
            except (TypeError, ValueError):
                continue
            if confidence < 0.70:
                continue
            text = normalize_space(records[block_id].get("text"))
            if (
                not text
                or len(text) > 220
                or len(WORD_RE.findall(text)) > 22
                or re.search(r"[{}=±∑∫√\\_]", text)
            ):
                continue
            seen_ids.add(block_id)
            selected_headings.append(
                {
                    "order": order_by_id[block_id],
                    "title": text,
                    "level": level,
                    "kind": (
                        "references"
                        if normalize_for_match(text) in REFERENCE_HEADINGS
                        else "section"
                    ),
                    "confidence": min(1.0, confidence),
                    "source_block_id": block_id,
                }
            )
        selected_headings.sort(key=lambda item: item["order"])
        current_sections = result.get("sections") or []
        current_confidence = (
            sum(float(item.get("confidence") or 0) for item in current_sections)
            / len(current_sections)
            if current_sections
            else 0
        )
        proposal_confidence = (
            sum(item["confidence"] for item in selected_headings)
            / len(selected_headings)
            if selected_headings
            else 0
        )
        if selected_headings and (
            not current_sections
            or (
                len(selected_headings) >= len(current_sections)
                and proposal_confidence > current_confidence + 0.05
            )
        ):
            rebuilt_sections = []
            ordered_records = list(records.items())
            for index, heading in enumerate(selected_headings):
                end = (
                    selected_headings[index + 1]["order"]
                    if index + 1 < len(selected_headings)
                    else len(ordered_records)
                )
                content = [
                    (
                        block_id,
                        normalize_space(record.get("text")),
                    )
                    for block_id, record in ordered_records[heading["order"] + 1 : end]
                    if normalize_space(record.get("text"))
                ]
                rebuilt_sections.append(
                    {
                        key: value
                        for key, value in heading.items()
                        if key != "order"
                    }
                    | {
                        "content": "\n".join(text for _block_id, text in content),
                        "content_block_ids": [
                            block_id for block_id, _text in content
                        ],
                    }
                )
            result["sections"] = rebuilt_sections
    remaining_review = []
    for item in result.get("needs_review") or []:
        field_name = item.get("field")
        if field_name == "sections":
            section_confidence = max(
                (
                    float(section.get("confidence") or 0)
                    for section in result.get("sections") or []
                ),
                default=0,
            )
            if section_confidence < 0.75:
                remaining_review.append(item)
            continue
        field = result.get(field_name) or {}
        confidence = (
            float(field.get("confidence") or 0)
            if isinstance(field, dict)
            else 0
        )
        if confidence < 0.75:
            remaining_review.append(item)
    result["needs_review"] = remaining_review
    return result
