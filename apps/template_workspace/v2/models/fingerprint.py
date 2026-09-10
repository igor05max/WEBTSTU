from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentFingerprint:
    paragraph_count: int
    table_count: int
    drawing_count: int
    image_count: int
    formula_count: int
    hyperlink_count: int
    bookmark_count: int
    section_count: int
    header_count: int
    footer_count: int
    relationship_count: int
    media_files: list[str]
    numbering_definitions: dict[str, int]
    normalized_text_hash: str
    table_structure_hash: str
    media_hashes: dict[str, str]
    formula_xml_hashes: list[str]
    critical_object_hashes: dict[str, Any] = field(default_factory=dict)
