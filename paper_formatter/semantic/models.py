from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SemanticRole = Literal[
    "title",
    "subtitle",
    "author",
    "affiliation",
    "abstract_heading",
    "abstract",
    "keywords",
    "section",
    "subsection",
    "subsubsection",
    "list_item",
    "figure_caption",
    "table_caption",
    "references_heading",
    "reference",
    "paragraph",
    "unknown",
]


class SemanticBlock(BaseModel):
    block_id: str
    order: int
    text: str
    style: str = ""
    font_size_pt: float | None = None
    bold_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    italic_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    alignment: str | None = None
    outline_level: int | None = None
    numbered_prefix: str | None = None
    numbering_level: int | None = None
    has_numbering: bool = False
    is_in_numbered_sequence: bool = False
    previous_text: str = ""
    next_text: str = ""
    rule_role: SemanticRole = "unknown"
    rule_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rule_reason: str = ""


class SemanticDecision(BaseModel):
    block_id: str
    role: SemanticRole
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    heading_level: int | None = Field(default=None, ge=1, le=6)
    normalized_text: str | None = None
    reason: str = ""
    source: Literal["rules", "local_ai", "fallback"] = "rules"


class SemanticAnalysis(BaseModel):
    provider: str
    decisions: list[SemanticDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_response: str | None = None

    def by_id(self) -> dict[str, SemanticDecision]:
        return {item.block_id: item for item in self.decisions}
