from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from apps.template_workspace.v2.models.document_info import DocumentReport, ParagraphInfo, SemanticRoleLayer
from apps.template_workspace.v2.models.template_profile import ArticleStructure


ROLE_NAMES = (
    "editorial_metadata",
    "article_type",
    "rubric",
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
    "funding_text",
    "acknowledgements_heading",
    "acknowledgements_text",
    "conflict_heading",
    "conflict_text",
    "author_information",
    "received_metadata",
    "copyright_metadata",
    "unknown",
)

FRONT_MATTER = "front_matter"
BODY = "body"
BACK_MATTER = "back_matter"
REFERENCES = "references"
AUTHOR_INFO = "author_info"


@dataclass
class RoleDecision:
    block_id: str
    role: str
    confidence: float
    source: str
    reason: str
    needs_review: bool = False
    heading_level: int | None = None
    zone: str | None = None
    language: str | None = None
    group_id: str | None = None
    subtype: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "role_hint": self.role,
            "confidence": round(self.confidence, 3),
            "heading_level": self.heading_level,
            "source": self.source,
            "reason": self.reason,
            "needs_review": self.needs_review,
            "zone": self.zone,
            "language": self.language,
            "group_id": self.group_id,
            "subtype": self.subtype,
        }


class RoleClassifierV2:
    """Deterministic structure classifier for scholarly DOCX files.

    The classifier intentionally does not call a network model.  A future model can be
    injected through ``semantic_provider`` for only the blocks that remain ambiguous.
    The deterministic layer remains the source of truth for zones, references and
    front-matter sequencing.
    """

    def __init__(
        self,
        *,
        use_ai: bool = False,
        review_threshold: float = 0.74,
        semantic_provider: Callable[[DocumentReport, list[dict[str, Any]]], dict[str, dict[str, Any]]] | None = None,
    ):
        self.use_ai = use_ai
        self.review_threshold = review_threshold
        self.semantic_provider = semantic_provider

    def classify(self, report: DocumentReport, *, template_mode: bool = False) -> SemanticRoleLayer:
        paragraphs = [p for p in report.paragraphs if p.normalized_text]
        if template_mode:
            paragraphs = _template_sample_paragraphs(paragraphs)
        decisions = self._classify_document(paragraphs)
        warnings: list[str] = []

        if self.use_ai and self.semantic_provider is not None:
            ambiguous = [d.to_dict() for d in decisions if d.needs_review or d.confidence < self.review_threshold]
            if ambiguous:
                try:
                    overrides = self.semantic_provider(report, ambiguous) or {}
                    by_id = {d.block_id: d for d in decisions}
                    for block_id, patch in overrides.items():
                        decision = by_id.get(block_id)
                        if decision is None:
                            continue
                        role = str(patch.get("role") or "")
                        confidence = float(patch.get("confidence") or 0)
                        if role not in ROLE_NAMES or confidence < 0.60:
                            continue
                        decision.role = role
                        decision.confidence = confidence
                        decision.needs_review = confidence < self.review_threshold
                        decision.source = "semantic_provider"
                        decision.reason = str(patch.get("reason") or "semantic provider override")
                except Exception as exc:  # semantic provider must never break the deterministic path
                    warnings.append(f"Semantic provider skipped: {exc}")
        elif self.use_ai:
            warnings.append("Semantic provider is not configured; deterministic V2 rules were used.")

        counts = Counter(d.role for d in decisions)
        return SemanticRoleLayer(
            provider="v2-context-rules" if not any(d.source == "semantic_provider" for d in decisions) else "v2-context-rules+provider",
            warnings=warnings,
            role_counts=dict(sorted(counts.items())),
            block_roles=[d.to_dict() for d in decisions],
        )

    def article_structure(self, report: DocumentReport) -> ArticleStructure:
        roles = self.classify(report)
        role_by_id = {item["block_id"]: item for item in roles.block_roles}
        blocks: list[dict[str, Any]] = []
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
                    "zone": role.get("zone"),
                    "language": role.get("language"),
                    "group_id": role.get("group_id"),
                    "subtype": role.get("subtype"),
                    "heading_level": role.get("heading_level"),
                }
            )
        return ArticleStructure(
            source_path=report.source_path,
            provider=roles.provider,
            warnings=roles.warnings,
            role_counts=roles.role_counts,
            blocks=blocks,
        )

    def _classify_document(self, paragraphs: list[ParagraphInfo]) -> list[RoleDecision]:
        if not paragraphs:
            return []

        decision_by_id: dict[str, RoleDecision] = {}
        front_end = _front_matter_end(paragraphs)
        references_start = _find_references_start(paragraphs)
        author_info_start = _find_author_info_start(paragraphs)
        translated_tail: dict[str, RoleDecision] = {}
        if references_start is not None:
            for idx in range(references_start + 1, len(paragraphs)):
                if not _starts_abstract(paragraphs[idx].normalized_text):
                    continue
                end = next((j for j in range(idx + 1, min(len(paragraphs), idx + 8)) if _is_keywords(paragraphs[j].normalized_text)), None)
                if end is None:
                    continue
                start = idx
                for j in range(max(references_start + 1, idx - 4), idx):
                    if _looks_like_author(paragraphs[j].normalized_text):
                        start = j
                        break
                self._classify_front_matter(paragraphs[start:end + 1], translated_tail)
                for decision in translated_tail.values():
                    decision.zone = BACK_MATTER

        front = paragraphs[:front_end]
        self._classify_front_matter(front, decision_by_id)

        heading_anchors = _heading_anchor_signatures(paragraphs[front_end:references_start if references_start is not None else len(paragraphs)])
        special_state: str | None = None
        for idx in range(front_end, len(paragraphs)):
            p = paragraphs[idx]
            text = p.normalized_text
            lowered = _strip_heading_number(text).casefold().strip(" .:")
            lang = _language(text)

            if references_start is not None and idx >= references_start:
                if p.id in translated_tail:
                    decision_by_id[p.id] = translated_tail[p.id]
                    continue
                if _is_references_heading(lowered):
                    decision_by_id[p.id] = RoleDecision(p.id, "references_heading", 0.99, "rules", "references zone anchor", heading_level=1, zone=REFERENCES, language=lang)
                    continue
                if _is_biography(text):
                    decision_by_id[p.id] = RoleDecision(p.id, "author_information", 0.95, "rules", "author biography marker", zone=AUTHOR_INFO, language=lang)
                    continue
                if re.match(r'^(?:Acknowledgements|Поддержка исследований|Funding)\.', text, re.I):
                    decision_by_id[p.id] = RoleDecision(p.id, "funding_text", 0.95, "rules", "inline funding marker", zone=BACK_MATTER, language=lang)
                    continue
                if author_info_start is not None and idx >= author_info_start:
                    decision_by_id[p.id] = self._classify_author_info_tail(p, lowered, lang)
                    continue
                if idx == references_start:
                    decision_by_id[p.id] = RoleDecision(p.id, "references_heading", 0.99, "rules", "references zone anchor", heading_level=1, zone=REFERENCES, language=lang)
                else:
                    decision_by_id[p.id] = RoleDecision(p.id, "reference_item", 0.96, "rules", "paragraph inside references zone", zone=REFERENCES, language=lang)
                continue

            special_role = _special_heading_role(lowered)
            if special_role:
                special_state = special_role.removesuffix("_heading")
                decision_by_id[p.id] = RoleDecision(
                    p.id,
                    special_role,
                    0.98,
                    "rules",
                    "back-matter heading marker",
                    heading_level=1,
                    zone=BACK_MATTER,
                    language=lang,
                )
                continue

            # Captions keep their object role even when they occur between a back-matter
            # heading and its translated counterpart.  Acknowledgement sections in real
            # journal files may still contain figures.
            if _is_figure_caption(text):
                decision_by_id[p.id] = RoleDecision(p.id, "figure_caption", 0.96, "rules", "caption syntax", zone=BODY, language=lang)
                continue
            if _is_table_caption(text):
                decision_by_id[p.id] = RoleDecision(p.id, "table_caption", 0.96, "rules", "table caption syntax", zone=BODY, language=lang)
                continue

            if special_state:
                next_role = f"{special_state}_text"
                decision_by_id[p.id] = RoleDecision(p.id, next_role, 0.93, "rules", "text following back-matter heading", zone=BACK_MATTER, language=lang)
                continue

            level, reason, confidence = _classify_heading(p, heading_anchors, first_body=(idx == front_end))
            if level:
                decision_by_id[p.id] = RoleDecision(p.id, f"heading_{level}", confidence, "rules", reason, heading_level=level, zone=BODY, language=lang)
                special_state = None
                continue

            special_state = None
            decision_by_id[p.id] = RoleDecision(p.id, "body", 0.94 if len(text) >= 100 else 0.82, "rules", "body-zone paragraph", zone=BODY, language=lang)

        decisions = [decision_by_id[p.id] for p in paragraphs]
        _refine_heading_hierarchy(paragraphs, decisions)
        _attach_caption_continuations(paragraphs, decisions)
        return decisions

    def _classify_front_matter(self, paragraphs: list[ParagraphInfo], out: dict[str, RoleDecision]) -> None:
        if not paragraphs:
            return

        citation_positions = [i for i, p in enumerate(paragraphs) if _is_citation(p.normalized_text)]
        groups: list[tuple[int, int]] = []
        start = 0
        for pos in citation_positions:
            groups.append((start, pos))
            start = pos + 1
        if not groups:
            ends = [i for i, p in enumerate(paragraphs) if _is_keywords(p.normalized_text)]
            if len(ends) > 1:
                groups = list(zip([0] + [i + 1 for i in ends[:-1]], ends))
            else:
                groups = [(0, len(paragraphs) - 1)]

        previous_end = -1
        group_no = 0
        for raw_start, end in groups:
            segment = list(range(raw_start, end + 1))
            author_idx = _best_author_index(paragraphs, segment)
            email_idx = _first_index(segment, lambda i: _is_email(paragraphs[i].normalized_text))
            abstract_idx = _first_index(segment, lambda i: _starts_abstract(paragraphs[i].normalized_text))
            keywords_idx = _first_index(segment, lambda i: _is_keywords(paragraphs[i].normalized_text))
            citation_idx = _first_index(segment, lambda i: _is_citation(paragraphs[i].normalized_text))

            if author_idx is None and raw_start == 0 and len(groups) > 1:
                previous_end = end
                continue

            title_idx = _best_title_index(paragraphs, segment, author_idx, abstract_idx)
            if title_idx is None:
                title_idx = _best_title_index(paragraphs, segment, email_idx, abstract_idx)

            if title_idx is None:
                for i in segment:
                    self._set_front_metadata(paragraphs, i, out)
                previous_end = end
                continue

            group_no += 1
            group_id = f"front_group_{group_no}"
            group_lang = _language(paragraphs[title_idx].normalized_text)
            author_indices = {i for i in segment if (abstract_idx is None or i < abstract_idx)
                              and i != title_idx and _looks_like_author(paragraphs[i].normalized_text)}
            if author_idx is not None:
                author_indices.add(author_idx)

            for i in segment:
                p = paragraphs[i]
                text = p.normalized_text
                lang = _language(text) or group_lang
                if i in author_indices:
                    out[p.id] = RoleDecision(p.id, "author", 0.96, "rules", "front-matter author line", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif i < title_idx:
                    self._set_front_metadata(paragraphs, i, out)
                elif i == title_idx:
                    out[p.id] = RoleDecision(p.id, "title", 0.97, "rules", "title immediately precedes author/affiliation block", zone=FRONT_MATTER, language=group_lang, group_id=group_id)
                elif re.search(r'(?:affiliation|аффилиац)', (p.style_name or '') + ' ' + text, re.I) and not re.match(r'^\*?\s*(?:Correspondence|для переписки)', text, re.I):
                    out[p.id] = RoleDecision(p.id, "affiliation", 0.97, "rules", "affiliation style or label", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif re.match(r'^\*?\s*(?:Correspondence|для переписки)', text, re.I):
                    out[p.id] = RoleDecision(p.id, "email", 0.99, "rules", "correspondence marker", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif author_idx is not None and i == author_idx:
                    out[p.id] = RoleDecision(p.id, "author", 0.96, "rules", "front-matter author line", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif email_idx is not None and i == email_idx:
                    out[p.id] = RoleDecision(p.id, "email", 0.99, "rules", "email syntax in front matter", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif abstract_idx is not None and abstract_idx <= i < (keywords_idx if keywords_idx is not None else end + 1):
                    out[p.id] = RoleDecision(p.id, "abstract", 0.97, "rules", "abstract range bounded by abstract/keywords markers", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif keywords_idx is not None and i == keywords_idx:
                    out[p.id] = RoleDecision(p.id, "keywords", 0.99, "rules", "keywords marker", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif citation_idx is not None and i == citation_idx:
                    out[p.id] = RoleDecision(p.id, "citation", 0.99, "rules", "citation marker", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif author_idx is not None and email_idx is not None and author_idx < i < email_idx:
                    out[p.id] = RoleDecision(p.id, "affiliation", 0.94, "rules", "paragraph between author and email in front matter", zone=FRONT_MATTER, language=lang, group_id=group_id)
                elif author_idx is not None and abstract_idx is not None and author_idx < i < abstract_idx and not _is_email(text):
                    out[p.id] = RoleDecision(p.id, "affiliation", 0.86, "rules", "front-matter block between author and abstract", zone=FRONT_MATTER, language=lang, group_id=group_id)
                else:
                    self._set_front_metadata(paragraphs, i, out, group_id=group_id)
            previous_end = end

        for p in paragraphs:
            if p.id not in out:
                self._set_front_metadata(paragraphs, paragraphs.index(p), out)

    @staticmethod
    def _set_front_metadata(paragraphs: list[ParagraphInfo], i: int, out: dict[str, RoleDecision], group_id: str | None = None) -> None:
        p = paragraphs[i]
        text = p.normalized_text
        lowered = text.casefold()
        subtype = None
        role = "editorial_metadata"
        confidence = 0.90
        if re.search(r"\b(тип статьи|article type|type of the paper)\b", lowered) or lowered in {"article", "review", "communication"}:
            role, subtype, confidence = "article_type", "article_type", 0.98
        elif re.search(r"\b(рубрика журнала|rubric|section)\s*:?$", lowered):
            subtype, confidence = "rubric_label", 0.98
        elif i > 0 and re.search(r"\b(рубрика журнала|rubric|section)\s*:?$", paragraphs[i - 1].normalized_text.casefold()):
            role, subtype, confidence = "rubric", "rubric_value", 0.97
        elif re.search(r"\bудк\b|\bdoi\s*:", lowered):
            subtype, confidence = "bibliographic_id", 0.99
        elif re.search(r"short communications|original papers|review articles|nobelistics", lowered):
            role, subtype, confidence = "rubric", "rubric_value", 0.95
        out[p.id] = RoleDecision(p.id, role, confidence, "rules", "front-matter metadata before title", zone=FRONT_MATTER, language=_language(text), group_id=group_id, subtype=subtype)

    @staticmethod
    def _classify_author_info_tail(p: ParagraphInfo, lowered: str, lang: str | None) -> RoleDecision:
        if _is_author_information_heading(lowered):
            return RoleDecision(p.id, "author_information", 0.98, "rules", "author information heading", heading_level=1, zone=AUTHOR_INFO, language=lang)
        if re.match(r"^(received|поступил|revised|accepted|принята)", lowered):
            return RoleDecision(p.id, "received_metadata", 0.98, "rules", "received/revised/accepted metadata", zone=AUTHOR_INFO, language=lang)
        if lowered.startswith("copyright") or "creative commons" in lowered:
            return RoleDecision(p.id, "copyright_metadata", 0.98, "rules", "copyright metadata", zone=AUTHOR_INFO, language=lang)
        return RoleDecision(p.id, "author_information", 0.88, "rules", "paragraph inside author-information tail", zone=AUTHOR_INFO, language=lang)


def _front_matter_end(paragraphs: list[ParagraphInfo]) -> int:
    last_citation = -1
    last_keywords = -1
    for i, p in enumerate(paragraphs[:80]):
        # Stop at the FIRST body anchor. Later translated summaries/keywords must
        # not swallow the entire paper into the front-matter zone.
        stripped = _strip_heading_number(p.normalized_text).casefold()
        if i > 0 and (re.match(r"^(?:introduction|введение)(?:[.\s:]|$)", stripped)
                      or re.match(r"^0\.\s+how to use", p.normalized_text, re.I)):
            return i
        if _is_citation(p.normalized_text):
            last_citation = i
        if _is_keywords(p.normalized_text):
            last_keywords = i
    anchor = max(last_citation, last_keywords)
    if anchor >= 0:
        return anchor + 1
    for i, p in enumerate(paragraphs):
        text = _strip_heading_number(p.normalized_text).casefold().strip(" .:")
        if text in {"introduction", "введение"}:
            return i
    return min(len(paragraphs), 8)


def _find_references_start(paragraphs: list[ParagraphInfo]) -> int | None:
    for i, p in enumerate(paragraphs):
        if _is_references_heading(_strip_heading_number(p.normalized_text).casefold()):
            return i
    return None


def _find_author_info_start(paragraphs: list[ParagraphInfo]) -> int | None:
    for i, p in enumerate(paragraphs):
        if _is_author_information_heading(_strip_heading_number(p.normalized_text).casefold()):
            return i
        lowered = p.normalized_text.casefold()
        if re.match(r"^received\b", lowered) or lowered.startswith("copyright"):
            return i
    return None


def _heading_anchor_signatures(paragraphs: list[ParagraphInfo]) -> dict[int, list[tuple[float, float, float, str]]]:
    anchors: dict[int, list[tuple[float, float, float, str]]] = defaultdict(list)
    for p in paragraphs:
        text = p.normalized_text
        match = re.match(r"^\s*(\d+(?:\.\d+){0,2})\.?(?:\s+|$)", text)
        if not match:
            continue
        level = min(3, match.group(1).count(".") + 1)
        anchors[level].append(_format_signature(p))
    return anchors


def _classify_heading(p: ParagraphInfo, anchors: dict[int, list[tuple[float, float, float, str]]], *, first_body: bool) -> tuple[int | None, str, float]:
    text = p.normalized_text
    if _is_figure_caption(text) or _is_table_caption(text) or len(text) > 220:
        return None, "", 0.0
    numeric = re.match(r"^\s*(\d+(?:\.\d+){0,2})\.?(?:\s+|$)", text)
    if numeric:
        level = min(3, numeric.group(1).count(".") + 1)
        return level, "explicit hierarchical number", 0.99

    if text.endswith(('.', ';', '?', '!')) and not first_body:
        return None, "", 0.0

    style = (p.style_name or p.style_id or "").casefold()
    if "heading 1" in style or "заголовок 1" in style:
        return 1, "Word heading style", 0.99
    if "heading 2" in style or "заголовок 2" in style:
        return 2, "Word heading style", 0.99
    if "heading 3" in style or "заголовок 3" in style:
        return 3, "Word heading style", 0.99

    bold = _bold_ratio(p)
    italic = _italic_ratio(p)
    align = _alignment(p)
    size = _dominant_size(p) or 0.0
    outline = p.properties.get("outline_level")
    candidate = first_body or outline is not None or bold >= 0.45 or (align == "center" and len(text) <= 150)
    if not candidate:
        return None, "", 0.0

    sig = _format_signature(p)
    scored: list[tuple[float, int]] = []
    for level, samples in anchors.items():
        if not samples:
            continue
        scored.append((min(_signature_distance(sig, sample) for sample in samples), level))
    if scored:
        distance, level = min(scored)
        if distance <= 2.4:
            return level, "formatting matched numbered heading anchors", max(0.82, 0.97 - distance * 0.05)

    if first_body:
        return 1, "first body block is a short heading-like paragraph", 0.90
    if italic >= 0.30 and bold >= 0.30:
        return 2, "bold/italic short heading pattern", 0.88
    if align == "center" and bold >= 0.40:
        return 1, "centered bold short heading", 0.86
    if bold >= 0.60 and size >= 10:
        return 2, "short bold subheading", 0.80
    return None, "", 0.0


def _format_signature(p: ParagraphInfo) -> tuple[float, float, float, str]:
    return (_dominant_size(p) or 0.0, _bold_ratio(p), _italic_ratio(p), _alignment(p))


def _signature_distance(a: tuple[float, float, float, str], b: tuple[float, float, float, str]) -> float:
    size = abs(a[0] - b[0]) / 2.0
    bold = abs(a[1] - b[1]) * 2.0
    italic = abs(a[2] - b[2]) * 1.5
    align = 0.0 if a[3] == b[3] else 0.8
    return size + bold + italic + align


def _best_author_index(paragraphs: list[ParagraphInfo], indices: list[int]) -> int | None:
    candidates: list[tuple[float, int]] = []
    for i in indices:
        text = paragraphs[i].normalized_text
        if _starts_abstract(text) or _is_keywords(text) or _is_citation(text):
            break
        score = 0.0
        if "©" in text:
            score += 4.0
        if _looks_like_author(text):
            score += 2.0
        if _looks_like_affiliation(text) or _is_email(text):
            score -= 3.0
        if score > 0:
            candidates.append((score, i))
    return max(candidates, key=lambda item: (item[0], -item[1]))[1] if candidates else None


def _best_title_index(paragraphs: list[ParagraphInfo], indices: list[int], author_idx: int | None, abstract_idx: int | None) -> int | None:
    upper = abstract_idx
    eligible = [i for i in indices if upper is None or i < upper]
    candidates: list[tuple[float, int]] = []
    for i in eligible:
        text = paragraphs[i].normalized_text
        lowered = text.casefold()
        style = (paragraphs[i].style_name or "").casefold()
        if re.search(r"(?:^|[_ .])title$|^название|^заглав", style) or lowered == "title":
            return i
        if len(text) < 18 or _is_email(text) or _looks_like_affiliation(text) or _looks_like_author(text):
            continue
        if _is_front_metadata_marker(lowered) or _starts_abstract(text) or _is_keywords(text) or lowered.strip(': ') in {'авторы', 'authors'}:
            continue
        score = 0.0
        score += max(0.0, 4.0 - (i - indices[0]) * 0.8)
        score += min(2.2, len(text) / 90)
        score += min(1.5, _bold_ratio(paragraphs[i]) * 2)
        if _alignment(paragraphs[i]) == "center":
            score += 1.5
        if text.endswith("."):
            score -= 1.5
        candidates.append((score, i))
    return max(candidates, default=(0.0, -1))[1] if candidates else None


def _first_index(indices: list[int], predicate) -> int | None:
    for i in indices:
        if predicate(i):
            return i
    return None


def _special_heading_role(lowered_without_number: str) -> str | None:
    value = lowered_without_number.strip(" .:")
    if re.fullmatch(r"(?:funding|финансирование)", value):
        return "funding_heading"
    if re.fullmatch(r"(?:acknowledg(?:e)?ments?|благодарности)", value):
        return "acknowledgements_heading"
    if re.fullmatch(r"(?:conflict(?:s)? of interests?|конфликт интересов)", value):
        return "conflict_heading"
    return None


def _refine_heading_hierarchy(paragraphs: list[ParagraphInfo], decisions: list[RoleDecision]) -> None:
    """Use the local heading sequence to refine unnumbered subheadings.

    If a section already contains an explicit level-2 heading, later unnumbered
    heading-like paragraphs before the next explicit level-1 heading belong to the
    same level unless their typography strongly contradicts it.  This is how real
    manuscripts encode sequences such as 2.1 + unnumbered 2.2/2.3/2.4.
    """
    current_h1 = False
    saw_h2 = False
    by_id = {d.block_id: d for d in decisions}
    for p in paragraphs:
        d = by_id[p.id]
        if d.zone != BODY or not d.role.startswith("heading_"):
            continue
        explicit = re.match(r"^\s*(\d+(?:\.\d+){0,2})\.?(?:\s+|$)", p.normalized_text)
        if d.role == "heading_1":
            if explicit:
                current_h1 = True
                saw_h2 = False
                continue
            if current_h1 and saw_h2:
                d.role = "heading_2"
                d.heading_level = 2
                d.confidence = max(d.confidence, 0.90)
                d.reason = "unnumbered heading continued a section that already contains level-2 headings"
                continue
            current_h1 = True
            saw_h2 = False
        elif d.role == "heading_2":
            saw_h2 = True


def _attach_caption_continuations(paragraphs: list[ParagraphInfo], decisions: list[RoleDecision]) -> None:
    by_id = {d.block_id: d for d in decisions}
    for i in range(1, len(paragraphs)):
        prev = by_id[paragraphs[i - 1].id]
        cur = by_id[paragraphs[i].id]
        if prev.role not in {"figure_caption", "table_caption"}:
            continue
        if cur.zone != BODY or cur.role not in {"body", "heading_1", "heading_2", "heading_3"}:
            continue
        text = paragraphs[i].normalized_text
        if len(text) > 220 or _looks_like_sentence(text):
            continue
        if _alignment(paragraphs[i]) == "center" or _format_similarity(paragraphs[i - 1], paragraphs[i]) >= 0.70:
            cur.role = prev.role
            cur.confidence = 0.91
            cur.needs_review = False
            cur.reason = "continuation of preceding caption"
            cur.group_id = prev.group_id or f"caption_{paragraphs[i - 1].id}"
            prev.group_id = cur.group_id


def _format_similarity(a: ParagraphInfo, b: ParagraphInfo) -> float:
    score = 0.0
    if _alignment(a) == _alignment(b):
        score += 0.4
    if abs((_dominant_size(a) or 0) - (_dominant_size(b) or 0)) <= 0.5:
        score += 0.3
    if abs(_bold_ratio(a) - _bold_ratio(b)) <= 0.2:
        score += 0.15
    if (a.style_id or "") == (b.style_id or ""):
        score += 0.15
    return score


def _looks_like_sentence(text: str) -> bool:
    if len(text) > 130:
        return True
    if re.match(r"^(fig\.|figure|рис\.?|рисунок|table|таблица)\s*\d+\s+(?:presents?|shows?|illustrates?)\b", text, flags=re.I):
        return True
    words = text.split()
    return len(words) > 22 and text.endswith(('.', '!', '?'))


def _is_front_metadata_marker(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(удк|doi|тип статьи|article type|рубрика журнала|rubric|short communications|original papers|review articles|nobelistics)\b",
            lowered,
        )
    )


def _is_references_heading(lowered: str) -> bool:
    return bool(re.fullmatch(r"(?:references|список литературы|литература|bibliography|(?:список|перечень) использованн(?:ых|ой) (?:источников|литературы)|библиографический список)", lowered.strip(" .:")))


def _is_biography(text: str) -> bool:
    return len(text) > 80 and bool(re.search(r'Research interests:|Область научных интересов|Научные интересы|—\s*(?:д-р|к\.т\.н|Ph\.D|Programmer|senior|младший|техник)', text, re.I))


def _is_author_information_heading(lowered: str) -> bool:
    value = lowered.strip(" .:")
    return value.startswith("information about the authors") or value.startswith("информация об авторах")


def _starts_abstract(text: str) -> bool:
    return bool(re.match(r"^(abstract|аннотация|резюме)\b", text.casefold()))


def _is_keywords(text: str) -> bool:
    return bool(re.match(r"^(keywords|key words|ключевые слова)\b", text.casefold()))


def _is_citation(text: str) -> bool:
    return bool(re.match(r"^(for citation|для цитирования)\b", text.casefold()))


def _is_figure_caption(text: str) -> bool:
    # Require punctuation after the figure number.  "Fig. 4 presents..." is body.
    return bool(re.match(r"^(?:fig\.|figure|рис\.?|рисунок)\s*\d+\s*[.\):\-–]\s*\S", text.strip(), flags=re.IGNORECASE))


def _is_table_caption(text: str) -> bool:
    return bool(re.match(r"^(?:table|таблица)\s*\d+\s*[.\):\-–]?\s+\S", text.strip(), flags=re.IGNORECASE))


def _is_email(text: str) -> bool:
    return bool(re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)) and len(text) < 220


def _looks_like_author(text: str) -> bool:
    lowered = text.casefold()
    if len(text) > 240 or _starts_abstract(text) or _is_keywords(text) or _is_citation(text) or any(marker in lowered for marker in ("university", "университет", "institute", "институт", "doi", "удк", "street", "ул.")):
        return False
    if "©" in text:
        return True
    # A citation contains authors followed by an article title. It is not an
    # additional author line merely because initials occur at its beginning.
    if len(text.split()) > 18 and not re.search(r'\[\d{4}-\d{4}-\d{4}-[\dX]{4}\]', text):
        return False
    if re.search(r"\[\d{4}-\d{4}-\d{4}-[\dX]{4}\]", text) and len(text) < 120:
        return True
    if re.fullmatch(r"[A-ZА-ЯЁ][\w-]+\s+[A-ZА-ЯЁ]\.\s*[A-ZА-ЯЁ]\.?(?:\s*[,;]\s*[A-ZА-ЯЁ][\w-]+\s+[A-ZА-ЯЁ]\.\s*[A-ZА-ЯЁ]\.?)?", text):
        return True
    # Initials + surname, or Latin full names separated by commas.
    initial_names = r"\b(?:[A-ZА-ЯЁ]\.\s*){1,3}[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]+|\b[A-ZА-ЯЁ][a-zа-яё-]+\s+(?:[A-ZА-ЯЁ]\.\s*){1,3}"
    if re.search(initial_names, text):
        residue = re.sub(initial_names, '', text)
        return len(re.findall(r'[A-Za-zА-Яа-яЁё]', residue)) < 8
    if re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][A-Za-z-]+", text) and ("," in text or ";" in text):
        return True
    return False


def _template_sample_paragraphs(paragraphs: list[ParagraphInfo]) -> list[ParagraphInfo]:
    """An instruction preamble is not a title/body formatting example."""
    for i, p in enumerate(paragraphs[:100]):
        if i >= 3 and re.match(r"^(?:УДК|UDC)\s*[:\d]", p.normalized_text, re.I):
            preamble = ' '.join(x.normalized_text for x in paragraphs[:i]).casefold()
            if 'образец' in preamble and ('оформлен' in preamble or 'требован' in preamble):
                return paragraphs[i:]
    return paragraphs


def _looks_like_affiliation(text: str) -> bool:
    return bool(re.search(r"\b(university|institute|academy|centre|center|department|laboratory|университет|институт|академ|центр|кафедр|лаборатор|российская федерация|russian federation)\b", text.casefold()))


def _strip_heading_number(text: str) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+){0,2}\.?\s+", "", text).strip()


def _language(text: str) -> str | None:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    cyr = sum("а" <= ch.casefold() <= "я" or ch.casefold() == "ё" for ch in letters)
    lat = sum("a" <= ch.casefold() <= "z" for ch in letters)
    if cyr >= max(2, lat * 1.3):
        return "ru"
    if lat >= max(2, cyr * 1.3):
        return "en"
    return "mixed"


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
    return bold / total if total else 0.0


def _italic_ratio(paragraph: ParagraphInfo) -> float:
    total = 0
    italic = 0
    for run in paragraph.runs:
        length = max(1, len(run.text))
        total += length
        if run.effective_formatting.get("italic") is True or run.font.get("italic") is True:
            italic += length
    return italic / total if total else 0.0


def _alignment(paragraph: ParagraphInfo) -> str:
    return str((paragraph.effective_formatting.get("paragraph") or {}).get("alignment") or paragraph.properties.get("alignment") or "")
