from __future__ import annotations

import json
import os
import re
import socket
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from pydantic import ValidationError

from apps.checks.ai_client import (
    AIProviderError,
    extract_response_text,
    generate_content,
    get_api_base_url,
    get_configured_model,
    is_ai_configured,
)
from apps.template_workspace.v2.models.document_info import DocumentReport, ParagraphInfo, SemanticRoleLayer
from apps.template_workspace.v2.models.template_profile import ArticleStructure


ROLE_NAMES = (
    "editorial_metadata",
    "title",
    "author",
    "affiliation",
    "email",
    "abstract",
    "keywords",
    "citation",
    "heading_1",
    "heading_2",
    "heading_3",
    "body",
    "figure_caption",
    "table_caption",
    "reference_item",
    "references_heading",
    "funding_heading",
    "acknowledgements_heading",
    "conflict_heading",
    "author_information",
    "unknown",
)


@dataclass
class RoleDecision:
    block_id: str
    role: str
    confidence: float
    source: str
    reason: str
    needs_review: bool = False
    heading_level: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "role_hint": self.role,
            "confidence": round(self.confidence, 3),
            "heading_level": self.heading_level,
            "source": self.source,
            "reason": self.reason,
            "needs_review": self.needs_review,
        }


class RoleClassifierV2:
    """Classifies V2 roles without using the legacy paper_formatter classifier."""

    def __init__(self, *, use_ai: bool = True, review_threshold: float = 0.72):
        self.use_ai = use_ai
        self.review_threshold = review_threshold

    def classify(self, report: DocumentReport) -> SemanticRoleLayer:
        paragraphs = [paragraph for paragraph in report.paragraphs if paragraph.normalized_text]
        decisions = [self._classify_one(paragraph, index, paragraphs) for index, paragraph in enumerate(paragraphs)]
        warnings: list[str] = []
        if self.use_ai and is_ai_configured() and _configured_endpoint_reachable():
            self._apply_ai_for_ambiguous(decisions, paragraphs, report.source_path, warnings)
        elif self.use_ai and is_ai_configured():
            warnings.append(f"Qwen endpoint {get_api_base_url()} сейчас недоступен по TCP; роли V2 определены локальными правилами.")
        counts = Counter(decision.role for decision in decisions)
        return SemanticRoleLayer(
            provider="v2-rules+qwen" if any(decision.source == "qwen" for decision in decisions) else "v2-rules",
            warnings=warnings,
            role_counts=dict(sorted(counts.items())),
            block_roles=[decision.to_dict() for decision in decisions],
        )

    def article_structure(self, report: DocumentReport) -> ArticleStructure:
        roles = self.classify(report)
        role_by_id = {item["block_id"]: item for item in roles.block_roles}
        blocks = []
        for block in report.flow:
            role = role_by_id.get(block.id, {})
            blocks.append(
                {
                    "id": block.id,
                    "kind": block.kind,
                    "index": block.index,
                    "text_preview": block.text_preview,
                    "detected_role": role.get("role_hint") or block.role_hint or block.kind,
                    "confidence": role.get("confidence", 1.0 if block.kind != "paragraph" else 0.5),
                    "needs_review": role.get("needs_review", False),
                    "object_id": block.object_id,
                }
            )
        return ArticleStructure(
            source_path=report.source_path,
            provider=roles.provider,
            warnings=roles.warnings,
            role_counts=roles.role_counts,
            blocks=blocks,
        )

    def _classify_one(self, paragraph: ParagraphInfo, index: int, paragraphs: list[ParagraphInfo]) -> RoleDecision:
        text = paragraph.normalized_text
        lowered = text.casefold()
        front = index < 40
        font_size = _dominant_size(paragraph)
        bold_ratio = _bold_ratio(paragraph)
        alignment = _effective_alignment(paragraph)
        numbered = bool(paragraph.numbering) or bool(re.match(r"^\s*(\d+(\.\d+)*|[IVXLC]+|[A-ZА-Я])[\).\s]+", text))
        style_name = (paragraph.style_name or paragraph.style_id or "").casefold()
        previous = paragraphs[index - 1].normalized_text if index else ""

        if _is_editorial_metadata(text, front):
            return RoleDecision(paragraph.id, "editorial_metadata", 0.95, "rules", "front matter marker, UDC/DOI/category/citation metadata")
        if "@" in text and len(text) <= 180:
            return RoleDecision(paragraph.id, "email", 0.92, "rules", "email-like short paragraph")
        if _is_references_heading(lowered):
            return RoleDecision(paragraph.id, "references_heading", 0.96, "rules", "references heading marker", heading_level=1)
        if re.search(r"\b(funding|финансирован)", lowered):
            return RoleDecision(paragraph.id, "funding_heading", 0.9, "rules", "funding heading marker", heading_level=1)
        if re.search(r"\b(acknowledg|благодарност)", lowered):
            return RoleDecision(paragraph.id, "acknowledgements_heading", 0.9, "rules", "acknowledgement heading marker", heading_level=1)
        if re.search(r"\b(conflict|конфликт интерес)", lowered):
            return RoleDecision(paragraph.id, "conflict_heading", 0.9, "rules", "conflict heading marker", heading_level=1)
        if _is_abstract(text, previous):
            return RoleDecision(paragraph.id, "abstract", 0.9, "rules", "abstract heading/text marker")
        if _is_keywords(text):
            return RoleDecision(paragraph.id, "keywords", 0.92, "rules", "keywords marker")
        if _is_figure_caption(text):
            return RoleDecision(paragraph.id, "figure_caption", 0.9, "rules", "caption marker with caption punctuation after number")
        if _is_table_caption(text):
            return RoleDecision(paragraph.id, "table_caption", 0.9, "rules", "table caption marker")
        if _looks_like_reference_item(text, previous):
            return RoleDecision(paragraph.id, "reference_item", 0.84, "rules", "reference-like numbered/list paragraph")
        heading_level = _heading_level(text, paragraph, style_name, numbered, font_size, bold_ratio)
        if heading_level:
            return RoleDecision(paragraph.id, f"heading_{heading_level}", 0.86, "rules", "heading formatting/numbering pattern", heading_level=heading_level)
        if front and _looks_like_author(text):
            return RoleDecision(paragraph.id, "author", 0.78, "rules", "front matter name-like paragraph")
        if front and _looks_like_affiliation(text):
            return RoleDecision(paragraph.id, "affiliation", 0.78, "rules", "front matter organization/address marker")
        if front and _looks_like_title(text, alignment, font_size, bold_ratio):
            return RoleDecision(paragraph.id, "title", 0.75, "rules", "front matter title-like formatting")
        if numbered and len(text) < 180 and not text.endswith("."):
            return RoleDecision(paragraph.id, "heading_2", 0.68, "rules", "numbered short paragraph needs heading/list review", needs_review=True, heading_level=2)
        return RoleDecision(paragraph.id, "body", 0.82 if len(text) > 120 else 0.68, "rules", "default body paragraph", needs_review=len(text) <= 80)

    def _apply_ai_for_ambiguous(
        self,
        decisions: list[RoleDecision],
        paragraphs: list[ParagraphInfo],
        document_name: str,
        warnings: list[str],
    ) -> None:
        candidates = [
            (decision, paragraph)
            for decision, paragraph in zip(decisions, paragraphs)
            if decision.needs_review or decision.confidence < self.review_threshold
        ][:25]
        if not candidates:
            return
        payload = {
            "systemInstruction": {"parts": [{"text": "Ты классификатор структурных ролей DOCX. Не переписывай текст. Верни JSON."}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "document_name": document_name,
                                    "allowed_roles": ROLE_NAMES,
                                    "blocks": [
                                        {
                                            "block_id": paragraph.id,
                                            "text": paragraph.normalized_text[:700],
                                            "style": paragraph.style_name or paragraph.style_id,
                                            "effective_formatting": paragraph.effective_formatting,
                                            "numbering": paragraph.numbering,
                                            "current_role": decision.role,
                                            "current_confidence": decision.confidence,
                                        }
                                        for decision, paragraph in candidates
                                    ],
                                    "response": {"decisions": [{"block_id": "block_0001", "role": "body", "confidence": 0.8, "reason": "short"}]},
                                },
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {"maxOutputTokens": 4096, "temperature": 0, "responseMimeType": "application/json"},
        }
        try:
            response, model = generate_content(payload, model=get_configured_model(getattr(settings, "AI_MODEL", "")), timeout=_v2_ai_timeout_seconds())
            raw = extract_response_text(response)
            ai_payload = json.loads(raw)
            by_id = {decision.block_id: decision for decision in decisions}
            for item in ai_payload.get("decisions", []):
                role = str(item.get("role") or "")
                block_id = str(item.get("block_id") or "")
                confidence = float(item.get("confidence") or 0)
                if role in ROLE_NAMES and block_id in by_id and confidence >= 0.64:
                    by_id[block_id].role = role
                    by_id[block_id].confidence = confidence
                    by_id[block_id].source = "qwen"
                    by_id[block_id].reason = str(item.get("reason") or f"Qwen {model}")
                    by_id[block_id].needs_review = confidence < self.review_threshold
        except (AIProviderError, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            warnings.append(f"Qwen не применён для спорных V2-ролей; оставлены локальные правила. {exc}")


def _v2_ai_timeout_seconds() -> int:
    fallback = min(int(getattr(settings, "AI_REQUEST_TIMEOUT", 120) or 120), 15)
    try:
        return max(3, int(os.getenv("TEMPLATE_V2_AI_TIMEOUT_SECONDS", fallback)))
    except (TypeError, ValueError):
        return fallback


def _configured_endpoint_reachable() -> bool:
    parsed = urlparse(get_api_base_url())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        timeout = max(0.2, float(os.getenv("TEMPLATE_V2_AI_CONNECT_TIMEOUT_SECONDS", "1.5")))
    except (TypeError, ValueError):
        timeout = 1.5
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _dominant_size(paragraph: ParagraphInfo) -> float | None:
    sizes: Counter[float] = Counter()
    for run in paragraph.runs:
        value = run.effective_formatting.get("size") or run.font.get("size")
        try:
            if value:
                sizes[float(value) / 2] += max(1, len(run.text))
        except (TypeError, ValueError):
            continue
    return sizes.most_common(1)[0][0] if sizes else None


def _bold_ratio(paragraph: ParagraphInfo) -> float:
    total = 0
    bold = 0
    for run in paragraph.runs:
        length = max(1, len(run.text))
        total += length
        if run.effective_formatting.get("bold") is True or run.font.get("bold") is True:
            bold += length
    return bold / total if total else 0


def _effective_alignment(paragraph: ParagraphInfo) -> str:
    return str((paragraph.effective_formatting.get("paragraph") or {}).get("alignment") or paragraph.properties.get("alignment") or "")


def _is_editorial_metadata(text: str, front: bool) -> bool:
    lowered = text.casefold()
    if re.search(r"\b(удк|doi|for citation|forcitation|citation|тип статьи|рубрика|short communications|nobelistics)\b", lowered):
        return True
    if front and len(text) < 80 and re.search(r"\b(article type|review|communication|editorial)\b", lowered):
        return True
    return False


def _is_references_heading(lowered: str) -> bool:
    return bool(re.match(r"^(references|список литературы|литература|bibliography)\b", lowered))


def _is_abstract(text: str, previous: str) -> bool:
    lowered = text.casefold()
    return bool(re.match(r"^(abstract|аннотация|резюме)\b", lowered) or previous.casefold() in {"abstract", "аннотация"})


def _is_keywords(text: str) -> bool:
    return bool(re.match(r"^(keywords|key words|ключевые слова)\b", text.casefold()))


def _is_figure_caption(text: str) -> bool:
    return bool(re.match(r"^(fig\.|figure|рис\.?|рисунок)\s*\d+[.\):\-–]\s+\S", text.strip(), flags=re.IGNORECASE))


def _is_table_caption(text: str) -> bool:
    return bool(re.match(r"^(table|таблица)\s*\d+[.\):\-–]?\s+\S", text.strip(), flags=re.IGNORECASE))


def _looks_like_reference_item(text: str, previous: str) -> bool:
    if _is_references_heading(previous.casefold()):
        return True
    return bool(re.match(r"^\s*\[?\d+\]?[\).]\s+.+(doi|https?://|//|изд|journal|vol\.|pp\.)", text, flags=re.IGNORECASE))


def _heading_level(text: str, paragraph: ParagraphInfo, style_name: str, numbered: bool, font_size: float | None, bold_ratio: float) -> int | None:
    if _is_editorial_metadata(text, True) or _is_figure_caption(text) or _is_table_caption(text):
        return None
    if "heading 1" in style_name or "заголовок 1" in style_name:
        return 1
    if "heading 2" in style_name or "заголовок 2" in style_name:
        return 2
    if "heading 3" in style_name or "заголовок 3" in style_name:
        return 3
    match = re.match(r"^\s*(\d+(?:\.\d+){0,2})\.?\s+\S", text)
    if match and len(text) < 220 and not text.endswith("."):
        return min(3, match.group(1).count(".") + 1)
    if len(text) < 140 and not text.endswith(".") and (bold_ratio >= 0.55 or paragraph.properties.get("outline_level") is not None):
        return 2 if numbered else 1
    if font_size and font_size >= 12 and len(text) < 160 and not text.endswith("."):
        return 1
    return None


def _looks_like_author(text: str) -> bool:
    if len(text) > 180 or any(marker in text.casefold() for marker in ("university", "институт", "doi", "удк")):
        return False
    return bool(re.search(r"\b[A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ]\.){1,2}\b|\b[A-ZА-ЯЁ]\.\s*[A-ZА-ЯЁ]\.\s*[A-ZА-ЯЁ][a-zа-яё]+", text))


def _looks_like_affiliation(text: str) -> bool:
    return bool(re.search(r"\b(university|institute|academy|department|laboratory|университет|институт|академ|кафедр|лаборатор)", text.casefold()))


def _looks_like_title(text: str, alignment: str, font_size: float | None, bold_ratio: float) -> bool:
    if len(text) < 25 or len(text) > 260 or text.endswith("."):
        return False
    if _is_editorial_metadata(text, True):
        return False
    return alignment in {"center", "both"} or bold_ratio >= 0.45 or bool(font_size and font_size >= 12)
