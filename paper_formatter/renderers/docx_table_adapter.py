from __future__ import annotations

from dataclasses import dataclass
import re

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn

from paper_formatter.models import TableBlock


@dataclass(frozen=True)
class TableColumnMeasure:
    min_width_mm: float
    preferred_width_mm: float
    source_width_mm: float | None
    header_text: str
    longest_word_chars: int
    longest_numeric_chars: int
    numeric_share: float


@dataclass(frozen=True)
class _LayoutCandidate:
    strategy: str
    widths_mm: list[float]
    score: float


@dataclass(frozen=True)
class TableLayoutDecision:
    mode: str
    widths_mm: list[float]
    usable_width_mm: float
    required_width_mm: float | None
    font_size_pt: float
    column_min_widths_mm: list[float]
    column_preferred_widths_mm: list[float]
    layout_score: float
    layout_strategy: str
    warnings: list[str]


class WideTableAdapter:
    """Adapts source Word tables to the template layout without rebuilding content."""

    def decide(
        self,
        block: TableBlock,
        *,
        column_width_mm: float,
        full_width_mm: float,
        template_columns: int,
        table_font_size_pt: float = 8.0,
        min_font_size_pt: float = 6.5,
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
        font_size = self._mode_font_size(mode, table_font_size_pt, min_font_size_pt)
        warnings: list[str] = []
        candidate, measures = self._best_candidate(
            block,
            columns,
            usable_width,
            font_size,
            base_font_size_pt=table_font_size_pt,
        )
        while candidate.score > 0 and font_size - 0.5 >= min_font_size_pt:
            font_size = round(font_size - 0.5, 1)
            candidate, measures = self._best_candidate(
                block,
                columns,
                usable_width,
                font_size,
                base_font_size_pt=table_font_size_pt,
            )
        if candidate.score > 0:
            warnings.append(
                "DOCX: table layout cannot fit safely after adaptation "
                f"({columns} columns, {font_size:g} pt, score {candidate.score:.1f})."
            )
        return TableLayoutDecision(
            mode=mode,
            widths_mm=candidate.widths_mm,
            usable_width_mm=usable_width,
            required_width_mm=round(required_width, 2) if required_width else None,
            font_size_pt=font_size,
            column_min_widths_mm=[round(item.min_width_mm, 2) for item in measures],
            column_preferred_widths_mm=[
                round(item.preferred_width_mm, 2) for item in measures
            ],
            layout_score=round(candidate.score, 2),
            layout_strategy=candidate.strategy,
            warnings=warnings,
        )

    def adapt_source_table_geometry_xml(
        self,
        table,
        block: TableBlock,
        usable_width_mm: float,
        widths_mm: list[float] | None = None,
    ) -> None:
        columns = self._column_count(block)
        widths_mm = widths_mm or self.column_widths_mm(
            block,
            columns,
            usable_width_mm,
        )
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
        *,
        font_size_pt: float = 8.0,
    ) -> list[float]:
        columns = max(1, columns)
        available = max(20.0, usable_width_mm)
        measures = WideTableAdapter.measure_columns(block, columns, font_size_pt)
        return WideTableAdapter._distribute_min_preferred(measures, available)

    @staticmethod
    def measure_columns(
        block: TableBlock,
        columns: int,
        font_size_pt: float,
    ) -> list[TableColumnMeasure]:
        columns = max(1, columns)
        source = WideTableAdapter._source_width_hints_mm(block, columns)
        char_mm = WideTableAdapter._char_width_mm(font_size_pt)
        header_rows = max(1, block.header_rows or 1)
        measures: list[TableColumnMeasure] = []
        for column in range(columns):
            values = WideTableAdapter._column_values(block, column)
            header_values = [
                row[column].strip()
                for row in block.rows[:header_rows]
                if column < len(row) and row[column].strip()
            ]
            body_values = values[len(header_values) :] or values
            numeric_share = (
                sum(WideTableAdapter._looks_numeric(value) for value in body_values)
                / len(body_values)
                if body_values
                else 0.0
            )
            header_text = " ".join(header_values)
            header_len = max((len(value) for value in header_values), default=0)
            longest_word = max(
                (WideTableAdapter._longest_word_len(value) for value in values),
                default=0,
            )
            longest_numeric = max(
                (
                    WideTableAdapter._longest_numeric_len(value)
                    for value in values
                    if WideTableAdapter._looks_numeric(value)
                ),
                default=0,
            )
            longest_cell = max((len(value) for value in values), default=6)
            short_numeric = WideTableAdapter._is_short_numeric_column(
                values,
                header_values,
                numeric_share,
            )
            header_key = re.sub(
                r"[^a-z0-9]+", "", header_text.casefold().replace("№", "no")
            )
            is_index_column = header_key in {"no", "n", "number"}
            is_value_column = header_key in {"value", "values", "result", "results"}
            if is_value_column:
                # A measurement value needs room for decimals and an uncertainty;
                # it cannot be treated like a compact ordinal-number column.
                min_width = max(
                    10.5,
                    min(max(longest_word, longest_numeric), 12) * char_mm * 1.25,
                )
                preferred = max(
                    14.0,
                    min_width + 3.0,
                    min(max(longest_word, longest_numeric), 16) * char_mm * 1.65,
                )
            elif short_numeric:
                if is_index_column:
                    min_width = 7.2
                    preferred = max(8.4, min(header_len, 6) * char_mm * 0.85)
                else:
                    min_width = max(
                        8.5,
                        min(max(longest_word, longest_numeric), 10) * char_mm * 1.05,
                    )
                    preferred = max(
                        12.0,
                        min_width + 3.0,
                        min(header_len, 8) * char_mm,
                    )
            elif numeric_share >= 0.6:
                min_width = max(
                    9.0,
                    min(max(longest_word, longest_numeric), 18) * char_mm * 1.05,
                )
                preferred = max(
                    min_width + 2.0,
                    min(header_len, 28) * char_mm * 0.82,
                    min(max(longest_word, longest_numeric), 22) * char_mm * 1.35,
                )
            else:
                min_width = max(8.0, min(longest_word, 26) * char_mm * 0.82)
                preferred = max(
                    min_width + 2.0,
                    min(header_len, 48) * char_mm * 0.72,
                    min(longest_word, 34) * char_mm * 1.05,
                    min(longest_cell, 70) * char_mm * 0.62,
                )
            measures.append(
                TableColumnMeasure(
                    min_width_mm=round(min_width, 2),
                    preferred_width_mm=round(max(min_width, preferred), 2),
                    source_width_mm=source[column],
                    header_text=header_text,
                    longest_word_chars=longest_word,
                    longest_numeric_chars=longest_numeric,
                    numeric_share=round(numeric_share, 3),
                )
            )
        return measures

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
        measures = WideTableAdapter.measure_columns(block, columns, font_size_pt=8.0)
        return sum(item.preferred_width_mm for item in measures)

    @staticmethod
    def _best_candidate(
        block: TableBlock,
        columns: int,
        usable_width_mm: float,
        font_size_pt: float,
        *,
        base_font_size_pt: float,
    ) -> tuple[_LayoutCandidate, list[TableColumnMeasure]]:
        available = max(20.0, usable_width_mm)
        measures = WideTableAdapter.measure_columns(block, columns, font_size_pt)
        candidates: list[_LayoutCandidate] = []
        source = WideTableAdapter._scaled_source_candidate(measures, available)
        if source is not None:
            candidates.append(
                _LayoutCandidate(
                    strategy="scaled_source",
                    widths_mm=source,
                    score=WideTableAdapter.layout_score(
                        block,
                        source,
                        measures,
                        font_size_pt,
                        base_font_size_pt=base_font_size_pt,
                    ),
                )
            )
        distributed = WideTableAdapter._distribute_min_preferred(measures, available)
        candidates.append(
            _LayoutCandidate(
                strategy="min_preferred",
                widths_mm=distributed,
                score=WideTableAdapter.layout_score(
                    block,
                    distributed,
                    measures,
                    font_size_pt,
                    base_font_size_pt=base_font_size_pt,
                ),
            )
        )
        return min(
            candidates,
            key=lambda item: (
                item.score,
                0 if item.strategy == "scaled_source" else 1,
            ),
        ), measures

    @staticmethod
    def _scaled_source_candidate(
        measures: list[TableColumnMeasure],
        available_width_mm: float,
    ) -> list[float] | None:
        source = [item.source_width_mm or 0.0 for item in measures]
        if not source or any(width <= 0 for width in source):
            return None
        source_sum = sum(source)
        if source_sum <= 0:
            return None
        average = source_sum / len(source)
        if min(source) > 0 and max(source) / min(source) < 1.15:
            return None
        if any(width > average * 6.0 for width in source):
            return None
        widths = [width * available_width_mm / source_sum for width in source]
        return WideTableAdapter._normalize_to_available(widths, available_width_mm)

    @staticmethod
    def _distribute_min_preferred(
        measures: list[TableColumnMeasure],
        available_width_mm: float,
    ) -> list[float]:
        available = max(20.0, available_width_mm)
        minimums = [item.min_width_mm for item in measures]
        preferred = [item.preferred_width_mm for item in measures]
        min_sum = sum(minimums)
        preferred_sum = sum(preferred)
        if preferred_sum <= available:
            widths = preferred[:]
            extra = available - preferred_sum
            weights = [
                (
                    0.5
                    if re.sub(
                        r"[^a-z0-9]+",
                        "",
                        item.header_text.casefold().replace("№", "no"),
                    )
                    in {"no", "n", "number"}
                    else max(1.0, item.preferred_width_mm)
                )
                for item in measures
            ]
        elif min_sum <= available:
            widths = minimums[:]
            extra = available - min_sum
            weights = [
                max(0.0, item.preferred_width_mm - item.min_width_mm)
                for item in measures
            ]
        else:
            scale = available / max(min_sum, 1.0)
            return [round(width * scale, 2) for width in minimums]
        weight_sum = sum(weights)
        if extra > 0:
            if weight_sum > 0:
                widths = [
                    width + extra * weights[index] / weight_sum
                    for index, width in enumerate(widths)
                ]
            else:
                widths = [width + extra / len(widths) for width in widths]
        return WideTableAdapter._normalize_to_available(widths, available)

    @staticmethod
    def _normalize_to_available(
        widths_mm: list[float],
        available_width_mm: float,
    ) -> list[float]:
        total = sum(widths_mm)
        if total > 0 and abs(total - available_width_mm) > 0.01:
            widths_mm = [width * available_width_mm / total for width in widths_mm]
        return [round(max(1.0, width), 2) for width in widths_mm]

    @staticmethod
    def layout_score(
        block: TableBlock,
        widths_mm: list[float],
        measures: list[TableColumnMeasure] | None = None,
        font_size_pt: float = 8.0,
        *,
        base_font_size_pt: float | None = None,
    ) -> float:
        measures = measures or WideTableAdapter.measure_columns(
            block,
            len(widths_mm),
            font_size_pt,
        )
        char_mm = WideTableAdapter._char_width_mm(font_size_pt)
        score = 0.0
        for index, measure in enumerate(measures):
            assigned = widths_mm[index] if index < len(widths_mm) else 0.0
            if assigned < measure.min_width_mm:
                score += (measure.min_width_mm - assigned) * 12.0
            numeric_required = measure.longest_numeric_chars * char_mm * 1.05
            if measure.longest_numeric_chars and assigned < numeric_required:
                score += (numeric_required - assigned) * 16.0
            word_required = measure.longest_word_chars * char_mm * 0.72
            if measure.longest_word_chars and assigned < word_required:
                score += (word_required - assigned) * 10.0
            if measure.header_text:
                header_required = min(len(measure.header_text), 42) * char_mm * 0.32
                if assigned < header_required:
                    score += (header_required - assigned) * 3.0
        return round(max(0.0, score), 3)

    @staticmethod
    def _source_width_hints_mm(block: TableBlock, columns: int) -> list[float]:
        raw = [
            max(1.0, float(width or 24.0) * 25.4 / 72)
            for width in block.column_widths_pt[:columns]
        ]
        raw.extend([24.0] * (columns - len(raw)))
        return raw[:columns]

    @staticmethod
    def _content_width_weights(
        block: TableBlock,
        columns: int,
        font_size_pt: float,
    ) -> list[float]:
        weights: list[float] = []
        char_mm = WideTableAdapter._char_width_mm(font_size_pt)
        header_rows = max(1, block.header_rows or 1)
        for column in range(columns):
            values = WideTableAdapter._column_values(block, column)
            header_values = [
                row[column].strip()
                for row in block.rows[:header_rows]
                if column < len(row) and row[column].strip()
            ]
            body_values = values[len(header_values) :] or values
            numeric_share = (
                sum(WideTableAdapter._looks_numeric(value) for value in body_values)
                / len(body_values)
                if body_values
                else 0.0
            )
            header_len = max((len(value) for value in header_values), default=0)
            longest_word = max(
                (WideTableAdapter._longest_word_len(value) for value in values),
                default=0,
            )
            longest_cell = max((len(value) for value in values), default=6)
            if WideTableAdapter._is_short_numeric_column(values, header_values, numeric_share):
                weight = max(3.0, min(header_len, 6) * char_mm * 0.55)
            elif numeric_share >= 0.6:
                weight = max(
                    7.0,
                    min(header_len, 18) * char_mm * 0.95,
                    min(longest_word, 14) * char_mm * 0.9,
                )
            else:
                weight = max(
                    8.0,
                    min(header_len, 42) * char_mm * 0.75,
                    min(longest_word, 28) * char_mm * 1.15,
                    min(longest_cell, 55) * char_mm * 0.38,
                )
            weights.append(weight)
        return weights

    @staticmethod
    def _minimum_widths_mm(
        block: TableBlock,
        columns: int,
        font_size_pt: float,
    ) -> list[float]:
        return [
            item.min_width_mm
            for item in WideTableAdapter.measure_columns(block, columns, font_size_pt)
        ]

    @staticmethod
    def _long_words_fit(
        block: TableBlock,
        widths_mm: list[float],
        font_size_pt: float,
    ) -> bool:
        measures = WideTableAdapter.measure_columns(block, len(widths_mm), font_size_pt)
        return WideTableAdapter.layout_score(block, widths_mm, measures, font_size_pt) == 0

    @staticmethod
    def _mode_font_size(
        mode: str,
        base_font_size_pt: float,
        min_font_size_pt: float,
    ) -> float:
        base = max(min_font_size_pt, base_font_size_pt)
        if mode == "wide":
            return max(min_font_size_pt, round(base - 1.0, 1))
        if mode == "medium":
            return max(min_font_size_pt, round(base - 0.5, 1))
        return base

    @staticmethod
    def _char_width_mm(font_size_pt: float) -> float:
        return max(0.9, font_size_pt * 25.4 / 72 * 0.5)

    @staticmethod
    def _column_values(block: TableBlock, column: int) -> list[str]:
        return [
            row[column].strip()
            for row in block.rows
            if column < len(row) and row[column].strip()
        ]

    @staticmethod
    def _longest_word_len(value: str) -> int:
        words = re.findall(r"[^\s/\\,;:()]+", value)
        return max((len(word) for word in words), default=0)

    @staticmethod
    def _longest_numeric_len(value: str) -> int:
        values = re.findall(r"[\d]+(?:[.,]\d+)?(?:\s*[±+\-/−–]\s*\d+(?:[.,]\d+)?)*", value)
        return max((len(re.sub(r"\s+", "", item)) for item in values), default=0)

    @staticmethod
    def _is_short_numeric_column(
        values: list[str],
        header_values: list[str],
        numeric_share: float,
    ) -> bool:
        header = " ".join(header_values).strip().casefold()
        return (
            header in {"no", "no.", "n", "#", "№"}
            or (numeric_share >= 0.8 and max((len(value) for value in values), default=0) <= 8)
        )

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
        return bool(value) and bool(re.fullmatch(r"[\d\s.,:+\-/%()±−–]+", value))
