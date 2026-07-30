from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SourceTrace(BaseModel):
    format: str
    location: str | None = None
    part: str | None = None
    xpath: str | None = None
    relationship_id: str | None = None
    page: int | None = None
    block_index: int | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TextRun(BaseModel):
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    asset_id: str | None = None
    formula_image: bool = False
    math_latex: str | None = None
    display: bool = False
    width_pt: float | None = None
    height_pt: float | None = None
    hyperlink: str | None = None
    citation_keys: list[str] = Field(default_factory=list)
    reference_target: str | None = None


class LocalizedText(BaseModel):
    language: str | None = None
    text: str


class Author(BaseModel):
    id: str
    name: str
    email: str | None = None
    orcid: str | None = None
    affiliation_ids: list[str] = Field(default_factory=list)


class Affiliation(BaseModel):
    id: str
    name: str


class ArticleMetadata(BaseModel):
    titles: list[LocalizedText] = Field(default_factory=list)
    subtitles: list[LocalizedText] = Field(default_factory=list)
    authors: list[Author] = Field(default_factory=list)
    author_variants: list[LocalizedText] = Field(default_factory=list)
    affiliations: list[Affiliation] = Field(default_factory=list)
    abstracts: list[LocalizedText] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    udc: str | None = None
    doi: str | None = None


class SectionBlock(BaseModel):
    type: Literal["section"] = "section"
    id: str
    title: str
    level: int = Field(default=1, ge=1, le=6)
    source: SourceTrace | None = None


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    id: str
    runs: list[TextRun] = Field(default_factory=list)
    source: SourceTrace | None = None

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


class ListItemBlock(BaseModel):
    type: Literal["list_item"] = "list_item"
    id: str
    runs: list[TextRun] = Field(default_factory=list)
    ordered: bool = False
    level: int = 0
    source: SourceTrace | None = None


class EquationBlock(BaseModel):
    type: Literal["equation"] = "equation"
    id: str
    latex: str
    label: str | None = None
    number: str | None = None
    display: bool = True
    source: SourceTrace | None = None


class FigureBlock(BaseModel):
    type: Literal["figure"] = "figure"
    id: str
    asset_id: str
    caption: str | None = None
    label: str | None = None
    group_id: str | None = None
    width_pt: float | None = None
    height_pt: float | None = None
    placement: str | None = None
    source: SourceTrace | None = None


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    id: str
    rows: list[list[str]] = Field(default_factory=list)
    caption: str | None = None
    label: str | None = None
    header_rows: int = 0
    column_widths_pt: list[float | None] = Field(default_factory=list)
    merged_cells: list["MergedTableCell"] = Field(default_factory=list)
    source: SourceTrace | None = None


class MergedTableCell(BaseModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)


class RawBlock(BaseModel):
    type: Literal["raw"] = "raw"
    id: str
    format: str
    content: str
    warning: str | None = None
    source: SourceTrace | None = None


ArticleBlock = Annotated[
    SectionBlock
    | ParagraphBlock
    | ListItemBlock
    | EquationBlock
    | FigureBlock
    | TableBlock
    | RawBlock,
    Field(discriminator="type"),
]


class ReferenceEntry(BaseModel):
    id: str
    text: str
    doi: str | None = None
    citation_key: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class NoteEntry(BaseModel):
    id: str
    kind: Literal["footnote", "endnote", "comment"] = "footnote"
    text: str
    source: SourceTrace | None = None


class CitationOccurrence(BaseModel):
    id: str
    keys: list[str] = Field(default_factory=list)
    raw_text: str
    source_block_id: str | None = None
    source: SourceTrace | None = None


class CrossReference(BaseModel):
    id: str
    target_id: str | None = None
    target_kind: Literal["equation", "figure", "table", "section", "bookmark", "unknown"] = "unknown"
    raw_text: str = ""
    source_block_id: str | None = None
    source: SourceTrace | None = None


class Asset(BaseModel):
    id: str
    path: str
    media_type: str | None = None
    original_name: str | None = None
    sha256: str | None = None


class ArticleIR(BaseModel):
    schema_version: str = "1.2"
    metadata: ArticleMetadata = Field(default_factory=ArticleMetadata)
    body: list[ArticleBlock] = Field(default_factory=list)
    references: list[ReferenceEntry] = Field(default_factory=list)
    citations: list[CitationOccurrence] = Field(default_factory=list)
    cross_references: list[CrossReference] = Field(default_factory=list)
    notes: list[NoteEntry] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    custom_latex: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    semantic_provider: str | None = None
    semantic_low_confidence: list[str] = Field(default_factory=list)


class PageLayout(BaseModel):
    paper_size: str = "a4paper"
    width_mm: float | None = None
    height_mm: float | None = None
    margin_top_mm: float = 20.0
    margin_right_mm: float = 20.0
    margin_bottom_mm: float = 20.0
    margin_left_mm: float = 20.0
    title_margin_left_mm: float | None = Field(default=None, ge=0)
    title_margin_right_mm: float | None = Field(default=None, ge=0)
    columns: int = Field(default=1, ge=1, le=4)
    column_gap_mm: float | None = None
    header_distance_mm: float | None = None
    footer_distance_mm: float | None = None


class TypographyProfile(BaseModel):
    main_font: str = "Times New Roman"
    main_size_pt: float = Field(default=12.0, gt=0)
    line_spacing: float = Field(default=1.15, gt=0)
    first_line_indent_mm: float = Field(default=12.5, ge=0)
    paragraph_space_before_pt: float = Field(default=0.0, ge=0)
    paragraph_space_after_pt: float = Field(default=0.0, ge=0)
    paragraph_alignment: Literal["left", "center", "right", "justify"] = "justify"
    title_font: str | None = None
    title_size_pt: float | None = None
    title_bold: bool = True
    author_size_pt: float | None = None
    abstract_size_pt: float | None = None
    caption_size_pt: float | None = None


class HeadingStyleProfile(BaseModel):
    level: int = Field(ge=1, le=6)
    font: str | None = None
    size_pt: float | None = None
    bold: bool = True
    italic: bool = False
    numbered: bool | None = None
    alignment: Literal["left", "center", "right", "justify"] = "left"
    space_before_pt: float | None = None
    space_after_pt: float | None = None


class LatexTemplateProfile(BaseModel):
    document_class: str = "article"
    class_options: list[str] = Field(default_factory=lambda: ["12pt"])
    packages: list[str] = Field(default_factory=list)
    preamble: str | None = None
    title_command: str = "title"
    author_command: str = "author"
    maketitle_command: str = "maketitle"
    bibliography_backend: Literal["thebibliography", "bibtex", "biblatex"] = "thebibliography"
    bibliography_style: str | None = None
    source_main_file: str | None = None


class TemplateProfile(BaseModel):
    schema_version: str = "1.0"
    name: str = "generic"
    source_path: str | None = None
    source_type: Literal["generic", "docx", "latex", "pdf", "requirements"] = "generic"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    page: PageLayout = Field(default_factory=PageLayout)
    typography: TypographyProfile = Field(default_factory=TypographyProfile)
    headings: list[HeadingStyleProfile] = Field(default_factory=list)
    latex: LatexTemplateProfile = Field(default_factory=LatexTemplateProfile)
    caption_position: Literal["above", "below", "mixed"] = "below"
    figure_width_fraction: float = Field(default=0.9, gt=0.0, le=1.0)
    requirements: list[str] = Field(default_factory=list)
    evidence: dict[str, str | float | int | bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PackageEntry(BaseModel):
    path: str
    size: int = Field(ge=0)
    compressed_size: int | None = Field(default=None, ge=0)
    extension: str = ""
    sha256: str | None = None


class PackageAnalysis(BaseModel):
    source_path: str
    source_type: Literal["file", "zip", "directory"]
    main_document: str | None = None
    document_type: str | None = None
    entries: list[PackageEntry] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConversionRun(BaseModel):
    run_id: str
    status: Literal["running", "completed", "completed_with_warnings", "failed"]
    current_stage: str
    source_path: str
    source_type: str
    output_path: str
    created_at: datetime
    updated_at: datetime
    article_ir_path: str | None = None
    template_profile_path: str | None = None
    latex_main_path: str | None = None
    latex_zip_path: str | None = None
    docx_path: str | None = None
    pdf_path: str | None = None
    validation_report_path: str | None = None
    html_report_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ConversionResult(BaseModel):
    run: ConversionRun
    article_ir: ArticleIR
    main_tex: Path
    latex_zip: Path
    template_profile: TemplateProfile | None = None
    docx: Path | None = None
    pdf: Path | None = None
