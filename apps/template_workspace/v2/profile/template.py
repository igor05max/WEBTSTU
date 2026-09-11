from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from apps.template_workspace.v2.classification.roles import ROLE_NAMES, RoleClassifierV2
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
)


class TemplateProfileBuilder:
    """Extracts formatting/layout rules from TEMPLATE, not its content."""

    def __init__(self, classifier: RoleClassifierV2 | None = None):
        self.classifier = classifier or RoleClassifierV2()

    def build(self, report: DocumentReport) -> TemplateProfile:
        roles = self.classifier.classify(report)
        role_by_id = {item["block_id"]: item for item in roles.block_roles}
        paragraphs_by_role: dict[str, list[tuple[ParagraphInfo, dict[str, Any]]]] = defaultdict(list)
        for paragraph in report.paragraphs:
            decision = role_by_id.get(paragraph.id)
            if not decision:
                continue
            role = decision.get("role_hint")
            if role in PROFILE_ROLES:
                paragraphs_by_role[role].append((paragraph, decision))
        role_profiles = {
            role: self._role_profile(role, items)
            for role, items in sorted(paragraphs_by_role.items())
            if items
        }
        missing = [role for role in PROFILE_ROLES if role not in role_profiles]
        return TemplateProfile(
            source_path=report.source_path,
            roles=role_profiles,
            layout=self._layout_profile(report),
            table_profiles=self._table_profiles(report),
            drawing_profiles=self._drawing_profiles(report),
            missing_roles=missing,
            warnings=roles.warnings,
        )

    def _role_profile(self, role: str, items: list[tuple[ParagraphInfo, dict[str, Any]]]) -> RoleFormatProfile:
        paragraph_formats = [clean(item.effective_formatting.get("paragraph", {})) for item, _ in items]
        run_formats = [clean(item.effective_formatting.get("run", {})) for item, _ in items]
        confidence = sum(float(decision.get("confidence") or 0) for _, decision in items) / max(1, len(items))
        return RoleFormatProfile(
            role=role,
            confidence=round(confidence, 3),
            examples_count=len(items),
            typical_paragraph_formatting=_most_common_dict(paragraph_formats),
            typical_run_formatting=_most_common_dict(run_formats),
            observed_variants=[
                {
                    "block_id": paragraph.id,
                    "style_id": paragraph.style_id,
                    "style_name": paragraph.style_name,
                    "paragraph": paragraph_formats[index],
                    "run": run_formats[index],
                }
                for index, (paragraph, _) in enumerate(items[:8])
            ],
        )

    def _layout_profile(self, report: DocumentReport) -> LayoutProfile:
        first = report.sections[0] if report.sections else None
        weighted_column_counts: Counter[int] = Counter()
        for section in report.sections:
            weighted_column_counts[column_count(section.columns)] += _section_weight(report, section.start_block, section.end_block)
        if not weighted_column_counts:
            weighted_column_counts[1] = 1
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
            default_body_column_count=weighted_column_counts.most_common(1)[0][0],
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


def column_count(columns: dict[str, Any]) -> int:
    for key, value in (columns or {}).items():
        if key.endswith("num"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return 1
    return 1


def _most_common_dict(values: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted({key for value in values for key in value})
    result: dict[str, Any] = {}
    for key in keys:
        serialized = Counter(json.dumps(jsonable(value.get(key)), sort_keys=True, ensure_ascii=False) for value in values if key in value)
        if serialized:
            result[key] = json.loads(serialized.most_common(1)[0][0])
    return result


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
