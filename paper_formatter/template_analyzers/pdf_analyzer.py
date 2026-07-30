from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from paper_formatter.exceptions import TemplateAnalysisError
from paper_formatter.models import (
    HeadingStyleProfile,
    PageLayout,
    TemplateProfile,
    TypographyProfile,
)
from paper_formatter.template_analyzers.base import TemplateAnalyzer


class PdfTemplateAnalyzer(TemplateAnalyzer):
    """Infer a reusable visual profile from rendered PDF geometry."""

    def analyze(self, source: Path) -> TemplateProfile:
        try:
            import pymupdf
        except ImportError as exc:
            raise TemplateAnalysisError(
                "Для анализа PDF установите PyMuPDF."
            ) from exc

        source = Path(source).resolve()
        try:
            document = pymupdf.open(source)
        except Exception as exc:
            raise TemplateAnalysisError(f"Не удалось открыть PDF-образец: {exc}") from exc
        if document.page_count == 0:
            document.close()
            raise TemplateAnalysisError("PDF-образец не содержит страниц.")

        page_count = document.page_count
        pages_sampled = min(page_count, 5)
        first_page = document[0]
        page_width = float(first_page.rect.width)
        page_height = float(first_page.rect.height)
        font_counter: Counter[str] = Counter()
        size_counter: Counter[float] = Counter()
        line_samples: list[dict[str, object]] = []

        for page_index in range(pages_sampled):
            current = document[page_index]
            data = current.get_text("dict")
            for block_index, block in enumerate(data.get("blocks", [])):
                if "lines" not in block:
                    continue
                for line_index, line in enumerate(block["lines"]):
                    spans = line.get("spans", [])
                    text = "".join(span.get("text", "") for span in spans).strip()
                    if not text:
                        continue
                    for span in spans:
                        span_text = span.get("text", "")
                        weight = max(1, len(span_text.strip()))
                        font_counter[span.get("font", "Unknown")] += weight
                        size_counter[round(float(span.get("size", 12.0)), 1)] += weight
                    representative = max(
                        spans,
                        key=lambda item: len(item.get("text", "").strip()),
                        default={},
                    )
                    bbox = tuple(float(value) for value in line["bbox"])
                    raw_font = representative.get("font", "Unknown")
                    line_samples.append(
                        {
                            "page": page_index,
                            "block": block_index,
                            "line": line_index,
                            "text": text,
                            "bbox": bbox,
                            "font": raw_font,
                            "size": round(float(representative.get("size", 12.0)), 1),
                            "bold": "bold" in raw_font.lower()
                            or bool(int(representative.get("flags", 0)) & 16),
                        }
                    )

        raw_font = (
            font_counter.most_common(1)[0][0]
            if font_counter
            else "Times New Roman"
        )
        main_size = size_counter.most_common(1)[0][0] if size_counter else 12.0
        main_font = self._word_font_name(raw_font)
        body_lines = [
            sample
            for sample in line_samples
            if sample["font"] == raw_font
            and abs(float(sample["size"]) - main_size) <= 0.35
            and len(str(sample["text"])) >= 12
            and self._line_width(sample) >= page_width * 0.15
        ]
        if len(body_lines) < 10:
            body_lines = [
                sample
                for sample in line_samples
                if abs(float(sample["size"]) - main_size) <= 0.35
                and len(str(sample["text"])) >= 12
                and self._line_width(sample) >= page_width * 0.15
            ]

        body_left_pt = self._modal_edge(
            [self._bbox(sample)[0] for sample in body_lines],
            fallback=56.7,
        )
        body_right_pt = self._percentile(
            [self._bbox(sample)[2] for sample in body_lines],
            0.95,
            fallback=page_width - 56.7,
        )
        column_bases = self._column_bases(body_lines, page_width, body_left_pt)
        columns = len(column_bases)
        column_gap_mm = self._column_gap_mm(body_lines, column_bases)

        top_margin_pt, bottom_margin_pt = self._vertical_margins(
            body_lines,
            pages_sampled=pages_sampled,
            page_height=page_height,
        )
        line_spacing = self._line_spacing(body_lines, main_size)
        first_line_indent_mm = self._first_line_indent_mm(
            body_lines,
            column_bases,
        )
        (
            title_font,
            title_size,
            title_margin_left_mm,
            title_margin_right_mm,
        ) = self._title_style(
            line_samples,
            main_font=main_font,
            main_size=main_size,
            page_height=page_height,
            page_width=page_width,
        )
        headings = self._heading_styles(
            line_samples,
            main_size=main_size,
            title_size=title_size,
        )
        caption_size = self._caption_size(size_counter, main_size)

        width_mm = page_width * 25.4 / 72
        height_mm = page_height * 25.4 / 72
        paper_size = (
            "a4paper"
            if abs(width_mm - 210) < 4 and abs(height_mm - 297) < 4
            else "letterpaper"
            if abs(width_mm - 215.9) < 4 and abs(height_mm - 279.4) < 4
            else "a4paper"
        )
        document.close()

        return TemplateProfile(
            name=source.stem,
            source_path=str(source),
            source_type="pdf",
            confidence=0.72 if len(body_lines) >= 30 else 0.58,
            page=PageLayout(
                paper_size=paper_size,
                width_mm=round(width_mm, 2),
                height_mm=round(height_mm, 2),
                margin_top_mm=round(top_margin_pt * 25.4 / 72, 2),
                margin_right_mm=round(
                    max(0.0, page_width - body_right_pt) * 25.4 / 72,
                    2,
                ),
                margin_bottom_mm=round(bottom_margin_pt * 25.4 / 72, 2),
                margin_left_mm=round(max(0.0, body_left_pt) * 25.4 / 72, 2),
                title_margin_left_mm=title_margin_left_mm,
                title_margin_right_mm=title_margin_right_mm,
                columns=columns,
                column_gap_mm=column_gap_mm,
            ),
            typography=TypographyProfile(
                main_font=main_font,
                main_size_pt=main_size,
                line_spacing=line_spacing,
                first_line_indent_mm=first_line_indent_mm,
                paragraph_space_before_pt=0.0,
                paragraph_space_after_pt=0.0,
                paragraph_alignment="justify",
                title_font=title_font,
                title_size_pt=title_size,
                abstract_size_pt=main_size,
                caption_size_pt=caption_size,
            ),
            headings=headings,
            evidence={
                "pages_sampled": pages_sampled,
                "pages_total": page_count,
                "font_samples": sum(font_counter.values()),
                "body_line_samples": len(body_lines),
                "raw_main_font": raw_font,
                "body_left_pt": round(body_left_pt, 2),
                "body_right_pt": round(body_right_pt, 2),
                "title_margin_left_mm_inferred": title_margin_left_mm,
                "title_margin_right_mm_inferred": title_margin_right_mm,
                "line_spacing_inferred": line_spacing,
                "first_line_indent_mm_inferred": first_line_indent_mm,
            },
            warnings=[
                "PDF-профиль восстанавливает геометрию и типографику основного текста, "
                "но не издательские команды, логотипы и служебные боковые блоки."
            ],
        )

    @staticmethod
    def _bbox(sample: dict[str, object]) -> tuple[float, float, float, float]:
        return sample["bbox"]  # type: ignore[return-value]

    @classmethod
    def _line_width(cls, sample: dict[str, object]) -> float:
        bbox = cls._bbox(sample)
        return bbox[2] - bbox[0]

    @staticmethod
    def _modal_edge(values: list[float], *, fallback: float) -> float:
        if not values:
            return fallback
        buckets = Counter(round(value) for value in values)
        return float(buckets.most_common(1)[0][0])

    @staticmethod
    def _percentile(
        values: list[float],
        fraction: float,
        *,
        fallback: float,
    ) -> float:
        if not values:
            return fallback
        ordered = sorted(values)
        index = round((len(ordered) - 1) * fraction)
        return float(ordered[index])

    @classmethod
    def _column_bases(
        cls,
        body_lines: list[dict[str, object]],
        page_width: float,
        fallback: float,
    ) -> list[float]:
        if not body_lines:
            return [fallback]
        buckets = Counter(
            round(cls._bbox(sample)[0] / 3) * 3
            for sample in body_lines
        )
        most_common = buckets.most_common()
        minimum_count = max(5, round(most_common[0][1] * 0.25))
        bases: list[float] = []
        for value, count in most_common:
            if count < minimum_count:
                continue
            if all(abs(value - existing) >= page_width * 0.18 for existing in bases):
                bases.append(float(value))
            if len(bases) == 4:
                break
        return sorted(bases or [fallback])

    @classmethod
    def _column_gap_mm(
        cls,
        body_lines: list[dict[str, object]],
        bases: list[float],
    ) -> float | None:
        if len(bases) < 2:
            return None
        first_column = [
            cls._bbox(sample)[2]
            for sample in body_lines
            if abs(cls._bbox(sample)[0] - bases[0])
            <= abs(cls._bbox(sample)[0] - bases[1])
        ]
        right_edge = cls._percentile(
            first_column,
            0.95,
            fallback=bases[1],
        )
        return round(max(0.0, bases[1] - right_edge) * 25.4 / 72, 2)

    @classmethod
    def _vertical_margins(
        cls,
        body_lines: list[dict[str, object]],
        *,
        pages_sampled: int,
        page_height: float,
    ) -> tuple[float, float]:
        by_page: dict[int, list[dict[str, object]]] = defaultdict(list)
        for sample in body_lines:
            by_page[int(sample["page"])].append(sample)
        preferred_pages = [
            index for index in range(1, pages_sampled) if by_page.get(index)
        ]
        page_indexes = preferred_pages or sorted(by_page)
        if not page_indexes:
            return 56.7, 56.7
        tops = [
            min(cls._bbox(sample)[1] for sample in by_page[index])
            for index in page_indexes
        ]
        bottoms = [
            page_height
            - max(cls._bbox(sample)[3] for sample in by_page[index])
            for index in page_indexes
        ]
        return (
            min(max(float(median(tops)), 14.0), 144.0),
            min(max(float(median(bottoms)), 14.0), 144.0),
        )

    @classmethod
    def _line_spacing(
        cls,
        body_lines: list[dict[str, object]],
        main_size: float,
    ) -> float:
        by_page: dict[int, list[float]] = defaultdict(list)
        for sample in body_lines:
            by_page[int(sample["page"])].append(cls._bbox(sample)[1])
        gaps: list[float] = []
        for values in by_page.values():
            ordered = sorted(set(round(value, 1) for value in values))
            for left, right in zip(ordered, ordered[1:]):
                gap = right - left
                if main_size * 1.05 <= gap <= main_size * 2.2:
                    gaps.append(gap)
        if not gaps or main_size <= 0:
            return 1.15
        return round(min(max(float(median(gaps)) / main_size, 1.0), 2.0), 2)

    @classmethod
    def _first_line_indent_mm(
        cls,
        body_lines: list[dict[str, object]],
        column_bases: list[float],
    ) -> float:
        by_block: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
        for sample in body_lines:
            by_block[(int(sample["page"]), int(sample["block"]))].append(sample)
        indents: list[float] = []
        multi_line_blocks = 0
        for samples in by_block.values():
            if len(samples) < 2:
                continue
            multi_line_blocks += 1
            first = min(samples, key=lambda item: int(item["line"]))
            first_x = cls._bbox(first)[0]
            base = min(column_bases, key=lambda value: abs(first_x - value))
            delta = first_x - base
            if 5.0 <= delta <= 72.0:
                indents.append(delta)
        if indents:
            return round(float(median(indents)) * 25.4 / 72, 2)
        return 0.0 if multi_line_blocks >= 3 else 12.5

    @classmethod
    def _title_style(
        cls,
        line_samples: list[dict[str, object]],
        *,
        main_font: str,
        main_size: float,
        page_height: float,
        page_width: float,
    ) -> tuple[str, float | None, float | None, float | None]:
        candidates = [
            sample
            for sample in line_samples
            if int(sample["page"]) == 0
            and cls._bbox(sample)[1] < page_height * 0.45
            and float(sample["size"]) >= main_size * 1.25
            and len(str(sample["text"])) >= 8
        ]
        if not candidates:
            return main_font, None, None, None
        title = max(candidates, key=lambda sample: float(sample["size"]))
        title_size = float(title["size"])
        title_lines = [
            sample
            for sample in candidates
            if abs(float(sample["size"]) - title_size) <= 0.75
        ]
        left_pt = min(cls._bbox(sample)[0] for sample in title_lines)
        right_pt = max(cls._bbox(sample)[2] for sample in title_lines)
        return (
            cls._word_font_name(str(title["font"])),
            title_size,
            round(max(0.0, left_pt) * 25.4 / 72, 2),
            round(max(0.0, page_width - right_pt) * 25.4 / 72, 2),
        )

    @classmethod
    def _heading_styles(
        cls,
        line_samples: list[dict[str, object]],
        *,
        main_size: float,
        title_size: float | None,
    ) -> list[HeadingStyleProfile]:
        sizes = Counter(
            float(sample["size"])
            for sample in line_samples
            if bool(sample["bold"])
            and main_size - 0.2 <= float(sample["size"])
            and (title_size is None or float(sample["size"]) < title_size - 0.5)
            and 4 <= len(str(sample["text"])) <= 160
            and cls._line_width(sample) >= 40.0
        )
        selected = sorted(sizes, key=float, reverse=True)[:3]
        return [
            HeadingStyleProfile(
                level=index,
                font=cls._word_font_name(
                    str(
                        next(
                            sample["font"]
                            for sample in line_samples
                            if abs(float(sample["size"]) - size) <= 0.05
                            and bool(sample["bold"])
                        )
                    )
                ),
                size_pt=size,
                bold=True,
                alignment="left",
                space_before_pt=round(size * 1.2, 1),
                space_after_pt=round(size * 0.5, 1),
            )
            for index, size in enumerate(selected, start=1)
        ]

    @staticmethod
    def _caption_size(size_counter: Counter[float], main_size: float) -> float | None:
        candidates = [
            (size, count)
            for size, count in size_counter.items()
            if 6.0 <= size < main_size - 0.5
        ]
        return max(candidates, key=lambda item: item[1])[0] if candidates else None

    @staticmethod
    def _word_font_name(raw_name: str) -> str:
        name = raw_name.split("+")[-1]
        lowered = name.lower()
        mappings = (
            (("urwpalladiol", "texgyrepagella", "pagella"), "Palatino Linotype"),
            (("timesnewroman", "nimbusrom", "texgyretermes"), "Times New Roman"),
            (("helvetica", "arial", "nimbussans"), "Arial"),
            (("courier", "nimbusmono"), "Courier New"),
            (("cambria",), "Cambria"),
        )
        for needles, replacement in mappings:
            if any(needle in lowered for needle in needles):
                return replacement
        for suffix in (
            "-Roman",
            "-Roma",
            "-Regular",
            "-Bold",
            "-Italic",
            "-Oblique",
        ):
            name = name.replace(suffix, "")
        return name or "Times New Roman"
