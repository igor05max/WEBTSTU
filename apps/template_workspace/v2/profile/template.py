from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from statistics import median
from typing import Any

from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.formatting.effective import clean
from apps.template_workspace.v2.models.document_info import DocumentReport, ParagraphInfo
from apps.template_workspace.v2.models.template_profile import (
    LayoutProfile,
    RoleFormatProfile,
    SectionRangeProfile,
    TemplateProfile,
)


PROFILE_ROLES = (
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
)


class TemplateProfileBuilder:
    """Extracts canonical formatting/layout rules from a TEMPLATE document.

    Only high-confidence examples are allowed to become canonical formatting.  The
    canonical profile is a *real representative paragraph*, not a property soup
    assembled from unrelated paragraphs.  Missing scholarly roles can be derived
    from close journal roles (for example heading_1 from References/Funding) but are
    explicitly marked synthetic.
    """

    def __init__(self, classifier: RoleClassifierV2 | None = None, *, min_confidence: float = 0.84):
        self.classifier = classifier or RoleClassifierV2(use_ai=False)
        self.min_confidence = min_confidence

    def build(self, report: DocumentReport) -> TemplateProfile:
        roles = self.classifier.classify(report, template_mode=True)
        role_by_id = {item["block_id"]: item for item in roles.block_roles}
        paragraphs_by_role: dict[str, list[tuple[ParagraphInfo, dict[str, Any]]]] = defaultdict(list)
        for paragraph in report.paragraphs:
            decision = role_by_id.get(paragraph.id)
            if not decision:
                continue
            role = decision.get("role_hint")
            if role in PROFILE_ROLES:
                paragraphs_by_role[role].append((paragraph, decision))

        role_profiles: dict[str, RoleFormatProfile] = {}
        for role, items in sorted(paragraphs_by_role.items()):
            profile = self._role_profile(role, items)
            if profile is not None:
                role_profiles[role] = profile

        self._synthesise_missing_roles(role_profiles)
        missing = [role for role in PROFILE_ROLES if role not in role_profiles]
        front_titles = [item for item in roles.block_roles if item.get("role_hint") == "title"]
        front_language_order = _unique([str(item.get("language")) for item in front_titles if item.get("language")])
        front_role_sequence = _front_role_sequence(roles.block_roles)

        return TemplateProfile(
            source_path=report.source_path,
            roles=role_profiles,
            layout=self._layout_profile(report),
            table_profiles=self._table_profiles(report),
            drawing_profiles=self._drawing_profiles(report),
            missing_roles=missing,
            warnings=roles.warnings,
            front_language_order=front_language_order,
            front_role_sequence=front_role_sequence,
            front_merge_roles=[role for role in ("abstract", "affiliation", "author")
                               if len(paragraphs_by_role.get(role, [])) == len(front_titles)
                               and bool(front_titles)],
        )

    def _role_profile(self, role: str, items: list[tuple[ParagraphInfo, dict[str, Any]]]) -> RoleFormatProfile | None:
        clean_items = [
            (paragraph, decision)
            for paragraph, decision in items
            if not bool(decision.get("needs_review")) and float(decision.get("confidence") or 0) >= self.min_confidence
        ]
        source_items = clean_items or items
        if not source_items:
            return None
        representative = _representative_paragraph([paragraph for paragraph, _ in source_items])
        representative_decision = next(decision for paragraph, decision in source_items if paragraph.id == representative.id)
        paragraph_format = clean(representative.effective_formatting.get("paragraph", {}))
        run_format = _representative_run_format(representative)
        confidence = sum(float(decision.get("confidence") or 0) for _, decision in source_items) / len(source_items)
        return RoleFormatProfile(
            role=role,
            confidence=round(confidence, 3),
            examples_count=len(items),
            clean_examples_count=len(clean_items),
            representative_block_id=representative.id,
            typical_paragraph_formatting=paragraph_format,
            typical_run_formatting=run_format,
            property_distributions=_property_distributions([paragraph for paragraph, _ in source_items]),
            observed_variants=[
                {
                    "block_id": paragraph.id,
                    "confidence": float(decision.get("confidence") or 0),
                    "style_id": paragraph.style_id,
                    "style_name": paragraph.style_name,
                    "paragraph": clean(paragraph.effective_formatting.get("paragraph", {})),
                    "run": _representative_run_format(paragraph),
                }
                for paragraph, decision in source_items[:10]
            ],
        )

    def _synthesise_missing_roles(self, profiles: dict[str, RoleFormatProfile]) -> None:
        heading_base = _first_profile(profiles, "references_heading", "funding_heading", "conflict_heading", "acknowledgements_heading")
        if "heading_1" not in profiles and heading_base is not None:
            profiles["heading_1"] = _derived_profile("heading_1", heading_base, bold=True, italic=False)
        if "heading_2" not in profiles:
            base = profiles.get("heading_1") or heading_base
            if base is not None:
                profiles["heading_2"] = _derived_profile("heading_2", base, bold=True, italic=True)
        if "heading_3" not in profiles and "heading_2" in profiles:
            profiles["heading_3"] = _derived_profile("heading_3", profiles["heading_2"], bold=False, italic=True)
        if "table_caption" not in profiles and "figure_caption" in profiles:
            profiles["table_caption"] = _derived_profile("table_caption", profiles["figure_caption"], bold=False, italic=False)
        if "article_type" not in profiles and "rubric" in profiles:
            profiles["article_type"] = _derived_profile("article_type", profiles["rubric"])
        for text_role in ("funding_text", "acknowledgements_text", "conflict_text"):
            if text_role not in profiles and "body" in profiles:
                profiles[text_role] = _derived_profile(text_role, profiles["body"])

    def _layout_profile(self, report: DocumentReport) -> LayoutProfile:
        first = report.sections[0] if report.sections else None
        weighted_column_counts: Counter[int] = Counter()
        for section in report.sections:
            weighted_column_counts[column_count(section.columns)] += _section_weight(report, section.start_block, section.end_block)
        if not weighted_column_counts:
            weighted_column_counts[1] = 1
        # Journal templates often have a large one-column front-matter section and a
        # two-column body.  If any substantial 2-column section exists, prefer 2 for
        # body rather than letting a long 1-column tail dominate the vote.
        two_col_weight = weighted_column_counts.get(2, 0)
        default_columns = 2 if two_col_weight >= 4 else weighted_column_counts.most_common(1)[0][0]
        section_ranges = [
            SectionRangeProfile(
                section_id=section.id,
                start_block=section.start_block,
                end_block=section.end_block,
                column_count=column_count(section.columns),
                previous_block=section.previous_block,
                next_block=section.next_block,
                section_type=section.section_type,
            )
            for section in report.sections
        ]
        return LayoutProfile(
            page_geometry=first.page_size if first else {},
            margins=first.margins if first else {},
            default_body_column_count=default_columns,
            column_spacing=_column_space(first.columns) if first else None,
            header_footer_pattern={
                "header_count": len(report.headers),
                "footer_count": len(report.footers),
                "page_number_fields": sum(item.page_number_field_count for item in report.headers + report.footers),
                "paragraph_borders": sum(item.paragraph_border_count for item in report.headers + report.footers),
            },
            first_page_pattern={
                "has_first_header": any(ref.get("type") == "first" for section in report.sections for ref in section.header_refs),
                "has_first_footer": any(ref.get("type") == "first" for section in report.sections for ref in section.footer_refs),
            },
            section_ranges=section_ranges,
            continuous_section_transition_patterns=self._transition_patterns(report),
        )

    def _transition_patterns(self, report: DocumentReport) -> list[dict[str, Any]]:
        patterns = []
        ranges = report.sections
        for left, middle, right in zip(ranges, ranges[1:], ranges[2:]):
            counts = [column_count(item.columns) for item in (left, middle, right)]
            if counts == [2, 1, 2] and middle.section_type in {None, "continuous"}:
                patterns.append(
                    {
                        "pattern": "two_columns_to_full_width_to_two_columns",
                        "sections": [left.id, middle.id, right.id],
                        "middle_range": {"start_block": middle.start_block, "end_block": middle.end_block},
                    }
                )
        return patterns

    def _table_profiles(self, report: DocumentReport) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for classification, tables in _group_by(report.tables, lambda table: table.classification).items():
            result[classification] = {
                "examples_count": len(tables),
                "typical_columns": Counter(table.logical_column_count for table in tables).most_common(1)[0][0],
                "typical_rows": Counter(table.row_count for table in tables).most_common(1)[0][0],
                "structure_hashes": [table.structure_hash for table in tables[:12]],
                "merge_topology_hashes": [table.merge_topology_hash for table in tables[:12]],
            }
        return result

    def _drawing_profiles(self, report: DocumentReport) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for kind, drawings in _group_by(report.drawings, lambda drawing: drawing.kind).items():
            result[kind] = {
                "examples_count": len(drawings),
                "inline_count": sum(1 for drawing in drawings if drawing.inline),
                "anchor_count": sum(1 for drawing in drawings if drawing.anchor),
                "has_media_count": sum(1 for drawing in drawings if drawing.media_target),
            }
        return result


def _representative_paragraph(paragraphs: list[ParagraphInfo]) -> ParagraphInfo:
    if len(paragraphs) == 1:
        return paragraphs[0]
    signatures = [_paragraph_signature(p) for p in paragraphs]
    best_index = 0
    best_score = float("inf")
    for i, sig in enumerate(signatures):
        score = sum(_signature_distance(sig, other) for other in signatures)
        # Prefer paragraphs with enough textual evidence over tiny accidental blocks.
        score -= min(1.0, len(paragraphs[i].normalized_text) / 250.0)
        if score < best_score:
            best_score = score
            best_index = i
    return paragraphs[best_index]


def _paragraph_signature(p: ParagraphInfo) -> tuple[float, float, float, str, float, float]:
    pformat = clean(p.effective_formatting.get("paragraph", {}))
    rformat = _representative_run_format(p)
    size = _as_float(rformat.get("size"), divisor=2.0)
    first_line = _nested_number(pformat.get("indentation", {}), "firstLine")
    line = _nested_number(pformat.get("spacing", {}), "line")
    return (
        size,
        _run_ratio(p, "bold"),
        _run_ratio(p, "italic"),
        str(pformat.get("alignment") or ""),
        first_line,
        line,
    )


def _signature_distance(a, b) -> float:
    return (
        abs(a[0] - b[0]) / 2.0
        + abs(a[1] - b[1]) * 2
        + abs(a[2] - b[2]) * 1.5
        + (0 if a[3] == b[3] else 0.8)
        + min(1.0, abs(a[4] - b[4]) / 500.0)
        + min(1.0, abs(a[5] - b[5]) / 250.0)
    )


def _representative_run_format(p: ParagraphInfo) -> dict[str, Any]:
    candidates = [clean(run.effective_formatting) for run in p.runs if run.text.strip()]
    if not candidates:
        return clean(p.effective_formatting.get("run", {}))
    # Choose the format carried by the most characters.  This avoids the bold
    # "Abstract." prefix becoming the base format for the whole abstract.
    weighted: Counter[str] = Counter()
    payloads: dict[str, dict[str, Any]] = {}
    for run in p.runs:
        if not run.text.strip():
            continue
        payload = clean(run.effective_formatting)
        key = json.dumps(jsonable(payload), sort_keys=True, ensure_ascii=False)
        weighted[key] += max(1, len(run.text))
        payloads[key] = payload
    return payloads[weighted.most_common(1)[0][0]]


def _property_distributions(paragraphs: list[ParagraphInfo]) -> dict[str, Any]:
    alignments = Counter()
    sizes: list[float] = []
    bold_ratios: list[float] = []
    italic_ratios: list[float] = []
    first_lines: list[float] = []
    lines: list[float] = []
    for p in paragraphs:
        pformat = clean(p.effective_formatting.get("paragraph", {}))
        if pformat.get("alignment"):
            alignments[str(pformat["alignment"])] += 1
        rformat = _representative_run_format(p)
        size = _as_float(rformat.get("size"), divisor=2.0)
        if size:
            sizes.append(size)
        bold_ratios.append(_run_ratio(p, "bold"))
        italic_ratios.append(_run_ratio(p, "italic"))
        first_line = _nested_number(pformat.get("indentation", {}), "firstLine")
        line = _nested_number(pformat.get("spacing", {}), "line")
        if first_line:
            first_lines.append(first_line)
        if line:
            lines.append(line)
    return {
        "alignment": dict(alignments),
        "font_size_pt": {"median": median(sizes) if sizes else None, "values": sorted(set(sizes))[:12]},
        "bold_ratio_median": median(bold_ratios) if bold_ratios else 0,
        "italic_ratio_median": median(italic_ratios) if italic_ratios else 0,
        "first_line_twips_median": median(first_lines) if first_lines else None,
        "line_twips_median": median(lines) if lines else None,
    }


def _run_ratio(p: ParagraphInfo, prop: str) -> float:
    total = 0
    active = 0
    for run in p.runs:
        length = max(1, len(run.text))
        total += length
        if run.effective_formatting.get(prop) is True or run.font.get(prop) is True:
            active += length
    return active / total if total else 0.0


def _derived_profile(role: str, base: RoleFormatProfile, *, bold: bool | None = None, italic: bool | None = None) -> RoleFormatProfile:
    paragraph = copy.deepcopy(base.typical_paragraph_formatting)
    run = copy.deepcopy(base.typical_run_formatting)
    if bold is not None:
        run["bold"] = bold
    if italic is not None:
        run["italic"] = italic
    return RoleFormatProfile(
        role=role,
        confidence=max(0.78, min(0.90, base.confidence - 0.04)),
        examples_count=0,
        clean_examples_count=0,
        representative_block_id=base.representative_block_id,
        typical_paragraph_formatting=paragraph,
        typical_run_formatting=run,
        property_distributions=copy.deepcopy(base.property_distributions),
        observed_variants=[],
        synthetic=True,
        derived_from=base.role,
    )


def _first_profile(profiles: dict[str, RoleFormatProfile], *roles: str) -> RoleFormatProfile | None:
    for role in roles:
        if role in profiles:
            return profiles[role]
    return None


def _front_role_sequence(block_roles: list[dict[str, Any]]) -> list[str]:
    first_group = next((item.get("group_id") for item in block_roles if item.get("role_hint") == "title" and item.get("group_id")), None)
    if not first_group:
        return []
    return _unique([
        str(item.get("role_hint"))
        for item in block_roles
        if item.get("group_id") == first_group and item.get("role_hint")
    ])


def _unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def column_count(columns: dict[str, Any]) -> int:
    for key, value in (columns or {}).items():
        if key.endswith("num"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return 1
    return 1


def _column_space(columns: dict[str, Any]) -> str | None:
    for key, value in (columns or {}).items():
        if key.endswith("space"):
            return str(value)
    return None


def _section_weight(report: DocumentReport, start_block: str | None, end_block: str | None) -> int:
    if not start_block or not end_block:
        return 1
    by_id = {block.id: block.index for block in report.flow}
    start = by_id.get(start_block)
    end = by_id.get(end_block)
    if start is None or end is None:
        return 1
    return max(1, end - start + 1)


def _as_float(value: Any, divisor: float = 1.0) -> float:
    try:
        return float(value) / divisor if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _nested_number(value: Any, suffix: str) -> float:
    if not isinstance(value, dict):
        return 0.0
    for key, raw in value.items():
        if str(key).endswith(suffix):
            return _as_float(raw)
    return 0.0


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def _group_by(items, key_func):
    groups = defaultdict(list)
    for item in items:
        groups[key_func(item)].append(item)
    return groups
