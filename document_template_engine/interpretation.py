from __future__ import annotations

import json
import re
from typing import Callable

from .schema import BLOCK_CATALOG, normalize_template_rules


TEMPLATE_RULE_SCHEMA = {
    "schema_version": "2.0",
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
    "document": {
        "blocks": [
            {
                "role": "",
                "label": "",
                "required": False,
                "aliases": [],
                "style": {
                    "alignment": "",
                    "first_line_indent_cm": None,
                    "font_family": "",
                    "font_size_pt": None,
                    "line_spacing": None,
                    "bold": None,
                    "italic": None,
                },
                "constraints": {
                    "uppercase": None,
                    "max_lines": None,
                    "terminal_period_allowed": None,
                },
            }
        ],
        "content_policy": {
            "preserve_original_text": True,
            "allow_scientific_rewrite": False,
            "insert_only_supplied_metadata": True,
        },
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
    "figures": {"captions_required": None, "caption_position": ""},
    "tables": {"captions_required": None, "caption_position": ""},
    "formulas": {"alignment": "", "number_alignment": ""},
    "languages": [],
    "filename_rule": "",
    "notes": [],
}


def build_interpretation_prompt(*, document_type: str, target_name: str, text: str) -> str:
    roles = ", ".join(BLOCK_CATALOG)
    return (
        "Ты анализируешь требования к оформлению научного документа. "
        "Извлекай только явно указанные или однозначно показанные в примере правила; "
        "ничего не придумывай. Верни один JSON-объект без Markdown по заданной схеме.\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "1. required_sections — только реальные названия разделов, которые должны буквально "
        "стоять отдельными заголовками (например, «Введение»). Не помещай туда подписи "
        "полей и элементы титульного блока.\n"
        "2. УДК, название, авторы, руководитель, организация, город/страна, аннотация, "
        "ключевые слова, основной текст и литература описываются в document.blocks.\n"
        "3. Строка-заполнитель «Abstract text» в примере тезисов обычно означает основной "
        "текст (role=body), а не обязательный раздел «Аннотация».\n"
        "4. Пометка optional/«при необходимости» означает required=false.\n"
        "5. Не требуй e-mail, аннотацию, ключевые слова или IMRAD-разделы, если источник "
        "не требует их явно.\n"
        "6. Научный текст нельзя переписывать или дополнять вымышленными фактами.\n\n"
        f"Допустимые роли блоков: {roles}.\n"
        f"Тип материала: {document_type}\n"
        f"Источник: {target_name}\n"
        f"Схема JSON: {json.dumps(TEMPLATE_RULE_SCHEMA, ensure_ascii=False)}\n\n"
        f"Текст требований или шаблона:\n{text[:120_000]}"
    )


def parse_json_object(value: str) -> dict:
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


def interpret_template_text(
    *,
    document_type: str,
    target_name: str,
    text: str,
    complete_json: Callable[[str], str],
) -> dict:
    prompt = build_interpretation_prompt(
        document_type=document_type,
        target_name=target_name,
        text=text,
    )
    normalized = normalize_template_rules(parse_json_object(complete_json(prompt)))
    source = str(text or "").casefold().replace("ё", "е")
    body_line_spacing = (normalized.get("body") or {}).get("line_spacing")
    for block in (normalized.get("document") or {}).get("blocks") or []:
        style = block.get("style")
        if not isinstance(style, dict):
            continue
        # Local models often serialize unspecified boolean values as false.
        # False must not erase the author's bold/italic formatting unless the
        # source explicitly requires regular/non-italic text.
        for key in ("bold", "italic"):
            if style.get(key) is False:
                style.pop(key, None)
        # "Ниже, через один интервал" describes vertical placement between
        # blocks, not a double line spacing inside the authors paragraph.
        if body_line_spacing not in (None, ""):
            style["line_spacing"] = body_line_spacing
        if block.get("role") == "references":
            references_context = ""
            match = re.search(
                r"(?:список\s+(?:использованн\w+\s+)?литератур\w*|references)",
                source,
            )
            if match:
                references_context = source[match.start() : match.start() + 350]
            if not re.search(r"(?:абзацн\w*\s+отступ|выравнив)", references_context):
                style.pop("first_line_indent_cm", None)
                style.pop("alignment", None)
    return normalized
