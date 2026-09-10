from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.checks.ai_client import get_configured_model, is_ai_configured
from apps.submissions.paper_formatter_ai import QwenSemanticProvider
from apps.template_workspace.v2.models.document_info import ParagraphInfo, SemanticRoleLayer
from paper_formatter.config import SemanticSettings
from paper_formatter.semantic.classifier import HybridSemanticClassifier
from paper_formatter.semantic.models import SemanticBlock


def classify_semantic_roles(paragraphs: list[ParagraphInfo], *, document_name: str, cache_dir: Path | None = None) -> SemanticRoleLayer:
    blocks = _semantic_blocks(paragraphs)
    if not blocks:
        return SemanticRoleLayer(provider="empty", warnings=[], role_counts={}, block_roles=[])

    ai_enabled = is_ai_configured()
    semantic_settings = SemanticSettings(
        enabled=ai_enabled,
        provider="qwen" if ai_enabled else "rules",
        model=get_configured_model(getattr(settings, "AI_MODEL", "")) if ai_enabled else "",
        timeout_seconds=getattr(settings, "AI_REQUEST_TIMEOUT", 120),
    )
    provider = QwenSemanticProvider(semantic_settings, cache_dir=cache_dir / "qwen" if cache_dir else None) if ai_enabled else None
    analysis = HybridSemanticClassifier(
        semantic_settings,
        cache_dir=cache_dir,
        provider=provider,
    ).analyze_document(blocks, document_name=document_name)
    decisions = analysis.decisions
    counts = Counter(decision.role for decision in decisions)
    return SemanticRoleLayer(
        provider=analysis.provider,
        warnings=list(analysis.warnings),
        role_counts=dict(sorted(counts.items())),
        block_roles=[
            {
                "block_id": decision.block_id,
                "role_hint": decision.role,
                "confidence": decision.confidence,
                "heading_level": decision.heading_level,
                "source": decision.source,
                "reason": decision.reason,
            }
            for decision in decisions
        ],
    )


def _semantic_blocks(paragraphs: list[ParagraphInfo]) -> list[SemanticBlock]:
    non_empty = [paragraph for paragraph in paragraphs if paragraph.normalized_text]
    number_counts = Counter(_numbered_prefix(paragraph.normalized_text) for paragraph in non_empty)
    result: list[SemanticBlock] = []
    for index, paragraph in enumerate(non_empty):
        previous_text = non_empty[index - 1].normalized_text if index else ""
        next_text = non_empty[index + 1].normalized_text if index + 1 < len(non_empty) else ""
        numbered_prefix = _numbered_prefix(paragraph.normalized_text)
        numbering = paragraph.numbering or {}
        result.append(
            SemanticBlock(
                block_id=paragraph.id,
                order=paragraph.index,
                text=paragraph.normalized_text,
                style=paragraph.style_name or paragraph.style_id or "",
                font_size_pt=_dominant_font_size(paragraph),
                bold_ratio=_format_ratio(paragraph, "bold"),
                italic_ratio=_format_ratio(paragraph, "italic"),
                alignment=paragraph.properties.get("alignment"),
                outline_level=_int_or_none(paragraph.properties.get("outline_level")),
                numbered_prefix=numbered_prefix,
                numbering_level=_int_or_none(numbering.get("ilvl")),
                has_numbering=bool(numbering),
                is_in_numbered_sequence=bool(numbered_prefix and number_counts[numbered_prefix] > 1),
                previous_text=previous_text,
                next_text=next_text,
            )
        )
    return result


def _numbered_prefix(text: str) -> str | None:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S", text or "")
    return match.group(1) if match else None


def _dominant_font_size(paragraph: ParagraphInfo) -> float | None:
    weighted: Counter[float] = Counter()
    for run in paragraph.runs:
        size = _half_points_to_points(run.font.get("size"))
        if size is not None:
            weighted[size] += max(1, len(run.text))
    return weighted.most_common(1)[0][0] if weighted else None


def _format_ratio(paragraph: ParagraphInfo, key: str) -> float:
    total = 0
    formatted = 0
    for run in paragraph.runs:
        length = max(1, len(run.text))
        total += length
        if run.font.get(key) is True:
            formatted += length
    return formatted / total if total else 0.0


def _half_points_to_points(value: Any) -> float | None:
    try:
        return round(float(value) / 2, 1) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
