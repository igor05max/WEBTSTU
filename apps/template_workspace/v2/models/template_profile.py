from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.template_workspace.v2.models.document_info import to_plain


@dataclass
class ArticleStructure:
    source_path: str
    provider: str
    warnings: list[str]
    role_counts: dict[str, int]
    blocks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)


@dataclass
class RoleFormatProfile:
    role: str
    confidence: float
    examples_count: int
    typical_paragraph_formatting: dict[str, Any]
    typical_run_formatting: dict[str, Any]
    observed_variants: list[dict[str, Any]] = field(default_factory=list)
    representative_block_id: str | None = None
    clean_examples_count: int = 0
    property_distributions: dict[str, Any] = field(default_factory=dict)
    synthetic: bool = False
    derived_from: str | None = None


@dataclass
class SectionRangeProfile:
    section_id: str
    start_block: str | None
    end_block: str | None
    column_count: int
    previous_block: str | None
    next_block: str | None
    section_type: str | None


@dataclass
class LayoutProfile:
    page_geometry: dict[str, Any]
    margins: dict[str, Any]
    default_body_column_count: int
    column_spacing: str | None
    header_footer_pattern: dict[str, Any]
    first_page_pattern: dict[str, Any]
    section_ranges: list[SectionRangeProfile]
    continuous_section_transition_patterns: list[dict[str, Any]]


@dataclass
class TemplateProfile:
    source_path: str
    roles: dict[str, RoleFormatProfile]
    layout: LayoutProfile
    table_profiles: dict[str, Any]
    drawing_profiles: dict[str, Any]
    missing_roles: list[str]
    warnings: list[str] = field(default_factory=list)
    front_language_order: list[str] = field(default_factory=list)
    front_role_sequence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)


@dataclass
class MappingPreview:
    source_path: str
    template_path: str
    mappings: list[dict[str, Any]]
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)
