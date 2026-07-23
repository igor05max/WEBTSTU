from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


SCHEMA_VERSION = "2.0"

BLOCK_CATALOG = {
    "udc": {
        "label": "Индекс УДК",
        "aliases": ["УДК", "UDC", "UDC index", "Индекс УДК"],
        "default_style": {"alignment": "left", "first_line_indent_cm": 0},
    },
    "title": {
        "label": "Название",
        "aliases": ["Название", "Заголовок", "Title", "Название тезисов"],
        "default_style": {"alignment": "center", "first_line_indent_cm": 0},
    },
    "authors": {
        "label": "Авторы",
        "aliases": [
            "Авторы",
            "Автор",
            "Author",
            "Authors",
            "Author initials and surname",
            "Инициалы и фамилия автора",
        ],
        "default_style": {"alignment": "center", "first_line_indent_cm": 0},
    },
    "supervisor": {
        "label": "Научный руководитель",
        "aliases": [
            "Научный руководитель",
            "Scientific supervisor",
            "Scientific supervisor (optional)",
        ],
        "default_style": {"alignment": "center", "first_line_indent_cm": 0},
    },
    "institution": {
        "label": "Организация",
        "aliases": [
            "Организация",
            "Название организации",
            "Название учреждения",
            "Institution",
            "Institution name",
        ],
        "default_style": {"alignment": "center", "first_line_indent_cm": 0},
    },
    "city_country": {
        "label": "Город и страна",
        "aliases": [
            "Город",
            "Страна",
            "Город, страна",
            "Город и страна",
            "City",
            "Country",
            "City, Country",
            "City and country",
        ],
        "default_style": {"alignment": "center", "first_line_indent_cm": 0},
    },
    "abstract": {
        "label": "Аннотация",
        "aliases": ["Аннотация", "Abstract"],
        "default_style": {"alignment": "justify"},
    },
    "keywords": {
        "label": "Ключевые слова",
        "aliases": ["Ключевые слова", "Keywords"],
        "default_style": {"alignment": "justify"},
    },
    "body": {
        "label": "Основной текст",
        "aliases": [
            "Основной текст",
            "Текст тезисов",
            "Текст тезисов доклада",
            "Body text",
            "Abstract text",
        ],
        "default_style": {"alignment": "justify"},
    },
    "references": {
        "label": "Список литературы",
        "aliases": [
            "Список литературы",
            "Список использованной литературы",
            "Литература",
            "References",
            "References (optional)",
        ],
        "default_style": {},
    },
}

DEFAULT_BLOCK_ORDER = (
    "udc",
    "title",
    "authors",
    "supervisor",
    "institution",
    "city_country",
    "abstract",
    "keywords",
    "body",
    "references",
)


def _normalize_label(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"\([^)]*(?:optional|необяз)[^)]*\)", "", text)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return " ".join(text.split())


_ROLE_ALIASES = {
    _normalize_label(alias): role
    for role, definition in BLOCK_CATALOG.items()
    for alias in definition["aliases"]
}


def role_from_label(value: Any) -> str:
    normalized = _normalize_label(value)
    if not normalized:
        return ""
    if normalized in _ROLE_ALIASES:
        return _ROLE_ALIASES[normalized]
    for alias, role in sorted(_ROLE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if len(alias) >= 5 and (normalized.startswith(alias) or alias.startswith(normalized)):
            return role
    return ""


def _is_optional_label(value: Any) -> bool:
    normalized = str(value or "").casefold()
    return "optional" in normalized or "необяз" in normalized or "при необходимости" in normalized


def _as_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    blocks = []
    for item in value:
        if isinstance(item, str):
            role = role_from_label(item)
            if role:
                blocks.append({"role": role, "label": item, "required": not _is_optional_label(item)})
            continue
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().casefold()
        if role not in BLOCK_CATALOG:
            role = role_from_label(item.get("label"))
        if not role:
            continue
        block = deepcopy(item)
        block["role"] = role
        block["required"] = bool(item.get("required", False))
        blocks.append(block)
    return blocks


def _merge_block(blocks: dict[str, dict[str, Any]], role: str, *, required=False, source_label=""):
    if role not in BLOCK_CATALOG:
        return
    existing = blocks.setdefault(
        role,
        {
            "role": role,
            "label": BLOCK_CATALOG[role]["label"],
            "required": False,
        },
    )
    existing["required"] = bool(existing.get("required")) or bool(required)
    if source_label and not existing.get("source_label"):
        existing["source_label"] = str(source_label)


def normalize_template_rules(rules: Any) -> dict[str, Any]:
    """
    Upgrade template rules to schema 2.0.

    Legacy AI responses often put front-matter placeholders such as ``Title`` or
    ``UDC index`` into ``required_sections``. They are document blocks, not
    literal headings, so they are moved to ``document.blocks`` here.
    """

    normalized = deepcopy(rules) if isinstance(rules, dict) else {}
    normalized["schema_version"] = SCHEMA_VERSION
    structure = normalized.setdefault("structure", {})
    if not isinstance(structure, dict):
        structure = {}
        normalized["structure"] = structure
    document_rules = normalized.setdefault("document", {})
    if not isinstance(document_rules, dict):
        document_rules = {}
        normalized["document"] = document_rules

    blocks_by_role: dict[str, dict[str, Any]] = {}
    block_order: list[str] = []
    for item in _as_blocks(document_rules.get("blocks")):
        role = item["role"]
        existing = blocks_by_role.get(role, {})
        blocks_by_role[role] = {**existing, **item}
        if role not in block_order:
            block_order.append(role)

    real_required_sections = []
    for label in structure.get("required_sections") or []:
        role = role_from_label(label)
        if role:
            if role not in block_order:
                block_order.append(role)
            _merge_block(
                blocks_by_role,
                role,
                required=not _is_optional_label(label),
                source_label=label,
            )
        else:
            real_required_sections.append(str(label))
    structure["required_sections"] = real_required_sections

    real_section_order = []
    for label in structure.get("section_order") or []:
        role = role_from_label(label)
        if role:
            if role not in block_order:
                block_order.append(role)
            _merge_block(
                blocks_by_role,
                role,
                required=False,
                source_label=label,
            )
        else:
            real_section_order.append(str(label))
    structure["section_order"] = real_section_order

    metadata = normalized.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        normalized["metadata"] = metadata
    unclassified_fields = []
    for label in metadata.get("required_fields") or []:
        role = role_from_label(label)
        if role:
            if role not in block_order:
                block_order.append(role)
            _merge_block(blocks_by_role, role, required=True, source_label=label)
        else:
            unclassified_fields.append(str(label))
    metadata["required_fields"] = unclassified_fields

    # A scientific document must contain body text, but the body is never
    # searched as a literal heading.
    _merge_block(blocks_by_role, "body", required=True)
    if "body" not in block_order:
        block_order.append("body")

    body_rules = normalized.get("body") if isinstance(normalized.get("body"), dict) else {}
    ordered_blocks = []
    ordered_roles = [
        *block_order,
        *(role for role in DEFAULT_BLOCK_ORDER if role not in block_order),
    ]
    for role in ordered_roles:
        block = blocks_by_role.get(role)
        if not block:
            continue
        definition = BLOCK_CATALOG[role]
        style = {**definition.get("default_style", {})}
        if role in {"body", "abstract", "keywords"}:
            for key in (
                "font_family",
                "font_size_pt",
                "line_spacing",
                "first_line_indent_cm",
                "alignment",
            ):
                if body_rules.get(key) not in (None, ""):
                    style[key] = body_rules[key]
        style.update(
            {
                key: value
                for key, value in (
                    block.get("style")
                    if isinstance(block.get("style"), dict)
                    else {}
                ).items()
                if value not in (None, "")
            }
        )
        block["label"] = str(block.get("label") or definition["label"])
        block["aliases"] = list(
            dict.fromkeys(
                [
                    *(definition.get("aliases") or []),
                    *(block.get("aliases") or []),
                ]
            )
        )
        block["style"] = style
        ordered_blocks.append(block)

    document_rules["blocks"] = ordered_blocks
    document_rules["block_order"] = [block["role"] for block in ordered_blocks]
    notes = " ".join(str(value) for value in (normalized.get("notes") or [])).casefold()
    title_block = next(
        (block for block in ordered_blocks if block.get("role") == "title"),
        None,
    )
    if title_block is not None and notes:
        constraints = dict(title_block.get("constraints") or {})
        if "title in uppercase" in notes or "назван" in notes and "прописн" in notes:
            constraints["uppercase"] = True
        if "max 2 lines" in notes or "не более двух строк" in notes:
            constraints["max_lines"] = 2
        if "no period at end" in notes or "без точки в конце" in notes:
            constraints["terminal_period_allowed"] = False
        title_block["constraints"] = constraints
    document_rules.setdefault(
        "content_policy",
        {
            "preserve_original_text": True,
            "allow_scientific_rewrite": False,
            "insert_only_supplied_metadata": True,
        },
    )
    return normalized


def get_document_blocks(rules: Any) -> list[dict[str, Any]]:
    return normalize_template_rules(rules).get("document", {}).get("blocks", [])
