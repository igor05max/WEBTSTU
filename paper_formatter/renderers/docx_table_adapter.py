from __future__ import annotations

from dataclasses import dataclass
import re

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn

from paper_formatter.models import TableBlock


@dataclass(frozen=True)
class TableLayoutDecision:
    mode: str
    widths_mm: list[float]
    usable_width_mm: float
    required_width_mm: float | None


class WideTableAdapter:
    """Adapts source Word tables to the template layout without rebuilding content."""

    def decide(
        self,
        block: TableBlock,
        *,
        column_width_mm: float,
        full_width_mm: float,
        template_columns: int,
    ) -> TableLayoutDecision:
        required_width = self.source_table_width_mm(block) or self.estimated_width_mm(block)
        columns = self._column_count(block)
        longest = self._longest_cell(block)
        if (
            template_columns > 1
            and (
                required_width > column_width_mm * 1.08
                or columns >= 4
                or (columns >= 3 and longest >= 18)
            )
        ):
            mode = "wide"
            usable_width = full_width_mm
        elif required_width > column_width_mm * 0.92:
            mode = "medium"
            usable_width = column_width_mm
        else:
            mode = "small"
            usable_width = min(column_width_mm, full_width_mm)
        return TableLayoutDecision(
            mode=mode,
            widths_mm=self.column_widths_mm(block, columns, usable_width),
            usable_width_mm=usable_width,
            required_width_mm=round(required_width, 2) if required_width else None,
        )

    def adapt_source_table_geometry_xml(
        self,
        table,
        block: TableBlock,
        usable_width_mm: float,
    ) -> None:
        columns = self._column_count(block)
        widths_mm = self.column_widths_mm(block, columns, usable_width_mm)
        widths_dxa = [max(240, round(width * 1440 / 25.4)) for width in widths_mm]

        tbl_pr = table.find(qn("w:tblPr"))
        if tbl_pr is None:
            tbl_pr = OxmlElement("w:tblPr")
            table.insert(0, tbl_pr)
        self._set_child_value(tbl_pr, "w:tblW", {"w:type": "dxa", "w:w": str(sum(widths_dxa))})
        self._set_child_value(tbl_pr, "w:tblLayout", {"w:type": "fixed"})
        self._set_child_value(tbl_pr, "w:jc", {"w:val": "center"})
        for removable in ("w:tblInd", "w:tblCellSpacing"):
            child = tbl_pr.find(qn(removable))
            if child is not None:
                tbl_pr.remove(child)

        grid = table.find(qn("w:tblGrid"))
        if grid is None:
            grid = OxmlElement("w:tblGrid")
            insert_at = 1 if table.find(qn("w:tblPr")) is not None else 0
            table.insert(insert_at, grid)
        for child in list(grid):
            grid.remove(child)
        for width in widths_dxa:
            column = OxmlElement("w:gridCol")
            column.set(qn("w:w"), str(width))
            grid.append(column)

        for row in table.xpath("./*[local-name()='tr']"):
            tr_pr = row.find(qn("w:trPr"))
            if tr_pr is not None:
                for height in list(tr_pr.findall(qn("w:trHeight"))):
                    tr_pr.remove(height)
            grid_index = 0
            for cell in row.xpath("./*[local-name()='tc']"):
                tc_pr = cell.find(qn("w:tcPr"))
                if tc_pr is None:
                    tc_pr = OxmlElement("w:tcPr")
                    cell.insert(0, tc_pr)
                span = self._cell_span(tc_pr)
                end = min(columns, grid_index + span)
                width = sum(widths_dxa[grid_index:end]) or widths_dxa[min(grid_index, columns - 1)]
                self._set_child_value(tc_pr, "w:tcW", {"w:type": "dxa", "w:w": str(width)})
                for removable in ("w:noWrap", "w:tcFitText"):
                    child = tc_pr.find(qn(removable))
                    if child is not None:
                        tc_pr.remove(child)
                grid_index += span

    @staticmethod
    def column_widths_mm(
        block: TableBlock,
        columns: int,
        usable_width_mm: float,
    ) -> list[float]:
        columns = max(1, columns)
        available = max(20.0, usable_width_mm)
        if block.column_widths_pt and any(
            width is not None and width > 0 for width in block.column_widths_pt
        ):
            raw = [
                max(1.0, float(width or 24.0) * 25.4 / 72)
                for width in block.column_widths_pt[:columns]
            ]
            raw.extend([24.0] * (columns - len(raw)))
        else:
            raw = []
            for column in range(columns):
                values = [
                    row[column].strip()
                    for row in block.rows
                    if column < len(row) and row[column].strip()
                ]
                body_values = values[block.header_rows :] or values
                numeric_share = (
                    sum(WideTableAdapter._looks_numeric(value) for value in body_values)
                    / len(body_values)
                    if body_values
                    else 0.0
                )
                longest = max((len(value) for value in values), default=6)
                if numeric_share >= 0.6:
                    raw.append(max(7.0, min(longest, 14) * 0.65))
                else:
                    raw.append(max(8.0, min(longest, 40) * 0.75))

        minimum = min(12.0, available / columns)
        widths = [available * value / sum(raw) for value in raw]
        fixed: set[int] = set()
        while True:
            new_fixed = {
                index
                for index, width in enumerate(widths)
                if width < minimum and index not in fixed
            }
            if not new_fixed:
                break
            fixed.update(new_fixed)
            remaining = available - minimum * len(fixed)
            flexible = [index for index in range(columns) if index not in fixed]
            flexible_weight = sum(raw[index] for index in flexible)
            for index in fixed:
                widths[index] = minimum
            for index in flexible:
                widths[index] = (
                    remaining * raw[index] / flexible_weight
                    if flexible_weight
                    else remaining / max(1, len(flexible))
                )
        return [round(value, 2) for value in widths]

    @staticmethod
    def source_table_width_mm(block: TableBlock) -> float | None:
        if block.column_widths_pt and any(
            width is not None and width > 0 for width in block.column_widths_pt
        ):
            return sum(float(width or 0.0) for width in block.column_widths_pt) * 25.4 / 72
        if not block.source_xml:
            return None
        try:
            element = parse_xml(block.source_xml)
        except Exception:
            return None
        values: list[int] = []
        for column in element.xpath("./*[local-name()='tblGrid']/*[local-name()='gridCol']"):
            raw = column.get(qn("w:w"))
            if raw:
                try:
                    values.append(int(raw))
                except ValueError:
                    pass
        if values:
            return sum(values) * 25.4 / 1440
        tbl_w = element.xpath("./*[local-name()='tblPr']/*[local-name()='tblW']")
        if tbl_w:
            raw = tbl_w[0].get(qn("w:w"))
            kind = tbl_w[0].get(qn("w:type"))
            if raw and kind == "dxa":
                try:
                    return int(raw) * 25.4 / 1440
                except ValueError:
                    return None
        return None

    @staticmethod
    def estimated_width_mm(block: TableBlock) -> float:
        columns = WideTableAdapter._column_count(block)
        widths = WideTableAdapter.column_widths_mm(block, columns, max(20.0, columns * 18.0))
        return sum(widths)

    @staticmethod
    def _column_count(block: TableBlock) -> int:
        return max(1, max((len(row) for row in block.rows), default=0))

    @staticmethod
    def _longest_cell(block: TableBlock) -> int:
        return max(
            (
                len(cell.strip())
                for row in block.rows
                for cell in row
                if cell.strip()
            ),
            default=0,
        )

    @staticmethod
    def _cell_span(tc_pr) -> int:
        span_el = tc_pr.find(qn("w:gridSpan"))
        if span_el is None:
            return 1
        try:
            return max(1, int(span_el.get(qn("w:val")) or "1"))
        except ValueError:
            return 1

    @staticmethod
    def _set_child_value(parent, tag: str, attrs: dict[str, str]) -> None:
        child = parent.find(qn(tag))
        if child is None:
            child = OxmlElement(tag)
            parent.append(child)
        for key, value in attrs.items():
            child.set(qn(key), value)

    @staticmethod
    def _looks_numeric(value: str) -> bool:
        return bool(value) and bool(re.fullmatch(r"[\d\s.,:+\-/%()]+", value))
