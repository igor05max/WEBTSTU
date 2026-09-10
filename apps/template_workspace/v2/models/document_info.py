from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


def to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


@dataclass
class PackageSummary:
    path: str
    part_count: int
    important_parts: list[str]
    media_files: list[str]
    relationship_count: int


@dataclass
class RunInfo:
    index: int
    text: str
    style_id: str | None
    font: dict[str, Any]
    direct_formatting: dict[str, Any]
    inherited_formatting: dict[str, Any]


@dataclass
class DrawingInfo:
    id: str
    block_id: str
    inline: bool
    anchor: bool
    relationship_id: str | None
    media_target: str | None
    media_sha256: str | None
    size: dict[str, Any]
    position: dict[str, Any]
    caption_nearby: str | None


@dataclass
class FormulaInfo:
    id: str
    block_id: str
    kind: str
    xml_hash: str
    display: bool


@dataclass
class HyperlinkInfo:
    id: str
    block_id: str
    relationship_id: str | None
    anchor: str | None
    target: str | None
    text: str


@dataclass
class ParagraphInfo:
    id: str
    index: int
    text: str
    normalized_text: str
    style_id: str | None
    style_name: str | None
    role_hint: str | None
    properties: dict[str, Any]
    direct_formatting: dict[str, Any]
    numbering: dict[str, Any] | None
    runs: list[RunInfo] = field(default_factory=list)
    drawings: list[DrawingInfo] = field(default_factory=list)
    formulas: list[FormulaInfo] = field(default_factory=list)
    hyperlinks: list[HyperlinkInfo] = field(default_factory=list)


@dataclass
class TableCellInfo:
    row: int
    grid_column: int
    grid_span: int
    v_merge: str | None
    width: dict[str, Any] | None
    text: str
    has_drawing: bool
    has_formula: bool
    nested_table_count: int


@dataclass
class TableInfo:
    id: str
    index: int
    row_count: int
    xml_cell_count: int
    logical_column_count: int
    grid: list[str | None]
    tbl_properties: dict[str, Any]
    rows: list[list[TableCellInfo]]
    merge_cells: list[dict[str, Any]]
    nested_table_count: int
    has_drawings: bool
    has_formulas: bool
    structure_hash: str
    caption_nearby: str | None


@dataclass
class FlowBlock:
    id: str
    kind: str
    index: int
    text_preview: str
    role_hint: str | None = None
    object_id: str | None = None


@dataclass
class SectionInfo:
    id: str
    index: int
    source: str
    page_size: dict[str, Any]
    margins: dict[str, Any]
    columns: dict[str, Any]
    section_type: str | None
    header_refs: list[dict[str, Any]]
    footer_refs: list[dict[str, Any]]
    page_numbering: dict[str, Any]


@dataclass
class HeaderFooterInfo:
    part: str
    kind: str
    paragraph_count: int
    text: str
    drawing_count: int
    field_count: int
    page_number_field_count: int
    paragraph_border_count: int
    relationships: list[dict[str, Any]]


@dataclass
class StyleInfo:
    style_id: str
    type: str
    name: str | None
    default: bool
    based_on: str | None
    next_style: str | None
    linked: str | None
    paragraph_properties: dict[str, Any]
    run_properties: dict[str, Any]


@dataclass
class NumberingLevelInfo:
    ilvl: str
    start: str | None
    num_format: str | None
    level_text: str | None
    paragraph_properties: dict[str, Any]
    run_properties: dict[str, Any]


@dataclass
class NumberingInfo:
    abstract_nums: list[dict[str, Any]]
    nums: list[dict[str, Any]]


@dataclass
class SemanticRoleLayer:
    provider: str
    warnings: list[str]
    role_counts: dict[str, int]
    block_roles: list[dict[str, Any]]


@dataclass
class DocumentReport:
    source_path: str
    package: PackageSummary
    flow: list[FlowBlock]
    paragraphs: list[ParagraphInfo]
    tables: list[TableInfo]
    drawings: list[DrawingInfo]
    formulas: list[FormulaInfo]
    hyperlinks: list[HyperlinkInfo]
    sections: list[SectionInfo]
    headers: list[HeaderFooterInfo]
    footers: list[HeaderFooterInfo]
    styles: list[StyleInfo]
    numbering: NumberingInfo
    semantic_roles: SemanticRoleLayer
    fingerprint: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)
