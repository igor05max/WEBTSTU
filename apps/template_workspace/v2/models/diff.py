from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .document_info import to_plain


@dataclass
class Change:
    category: str
    change_type: str
    source: str
    target: str
    before: Any
    after: Any
    classification: str


@dataclass
class DocumentDiff:
    source_path: str
    target_path: str
    summary: dict[str, Any]
    layout: list[Change] = field(default_factory=list)
    sections: list[Change] = field(default_factory=list)
    styles: list[Change] = field(default_factory=list)
    paragraph_changes: list[Change] = field(default_factory=list)
    table_changes: list[Change] = field(default_factory=list)
    drawing_changes: list[Change] = field(default_factory=list)
    formula_changes: list[Change] = field(default_factory=list)
    header_changes: list[Change] = field(default_factory=list)
    footer_changes: list[Change] = field(default_factory=list)
    flow_changes: list[Change] = field(default_factory=list)
    content_changes: list[Change] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)
