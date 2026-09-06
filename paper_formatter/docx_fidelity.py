from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re

from docx import Document
from docx.oxml.ns import qn


@dataclass(frozen=True)
class DocxLayoutSignature:
    width_mm: float
    height_mm: float
    margins_mm: tuple[float, float, float, float]
    column_counts: frozenset[int]
    maximum_columns: int
    column_gap_mm: float | None
    header_text: str
    main_font: str
    main_size_pt: float | None


def layout_signature(path: Path) -> DocxLayoutSignature:
    document = Document(Path(path))
    section = document.sections[0]
    column_counts: set[int] = set()
    gaps: list[float] = []
    for item in document.sections:
        columns = item._sectPr.xpath("./w:cols")
        count = 1
        if columns:
            raw_count = columns[-1].get(qn("w:num"))
            raw_gap = columns[-1].get(qn("w:space"))
            if raw_count and raw_count.isdigit():
                count = max(1, int(raw_count))
            if raw_gap and raw_gap.isdigit() and count > 1:
                gaps.append(int(raw_gap) * 25.4 / 1440)
        column_counts.add(count)

    header_text = ""
    for item in document.sections:
        candidate = " ".join(
            paragraph.text.strip()
            for paragraph in item.header.paragraphs
            if paragraph.text.strip()
        )
        if candidate:
            header_text = candidate
            break

    normal = document.styles["Normal"]
    main_font = normal.font.name or ""
    main_size = normal.font.size.pt if normal.font.size is not None else None
    return DocxLayoutSignature(
        width_mm=round(section.page_width.mm, 2),
        height_mm=round(section.page_height.mm, 2),
        margins_mm=tuple(
            round(value.mm, 2)
            for value in (
                section.top_margin,
                section.right_margin,
                section.bottom_margin,
                section.left_margin,
            )
        ),
        column_counts=frozenset(column_counts),
        maximum_columns=max(column_counts),
        column_gap_mm=round(sum(gaps) / len(gaps), 2) if gaps else None,
        header_text=_normalise_header(header_text),
        main_font=main_font.casefold(),
        main_size_pt=main_size,
    )


def source_already_matches_template(source: Path, template: Path) -> bool:
    """Return true when rebuilding a DOCX would only reduce its fidelity."""
    if Path(source).suffix.lower() != ".docx" or Path(template).suffix.lower() != ".docx":
        return False
    try:
        source_layout = layout_signature(source)
        template_layout = layout_signature(template)
    except Exception:
        return False

    if abs(source_layout.width_mm - template_layout.width_mm) > 0.8:
        return False
    if abs(source_layout.height_mm - template_layout.height_mm) > 0.8:
        return False
    if any(
        abs(left - right) > 0.8
        for left, right in zip(source_layout.margins_mm, template_layout.margins_mm)
    ):
        return False
    if source_layout.maximum_columns != template_layout.maximum_columns:
        return False
    if not template_layout.column_counts.issubset(source_layout.column_counts):
        return False
    if source_layout.maximum_columns > 1:
        if source_layout.column_gap_mm is None or template_layout.column_gap_mm is None:
            return False
        if abs(source_layout.column_gap_mm - template_layout.column_gap_mm) > 0.8:
            return False
    if source_layout.main_font and template_layout.main_font:
        if source_layout.main_font != template_layout.main_font:
            return False
    if source_layout.main_size_pt and template_layout.main_size_pt:
        if abs(source_layout.main_size_pt - template_layout.main_size_pt) > 0.75:
            return False
    if template_layout.header_text:
        if not source_layout.header_text:
            return False
        if SequenceMatcher(
            None,
            source_layout.header_text,
            template_layout.header_text,
            autojunk=False,
        ).ratio() < 0.82:
            return False
    return True


def _normalise_header(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return re.sub(r"\d+", "#", value)
