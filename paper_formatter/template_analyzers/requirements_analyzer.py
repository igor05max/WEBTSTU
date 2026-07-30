from __future__ import annotations

import re
from pathlib import Path

from paper_formatter.models import PageLayout, TemplateProfile, TypographyProfile
from paper_formatter.template_analyzers.base import TemplateAnalyzer


class RequirementsTemplateAnalyzer(TemplateAnalyzer):
    def analyze(self, source: Path) -> TemplateProfile:
        source = Path(source).resolve()
        text = source.read_text(encoding="utf-8", errors="replace")
        font_match = re.search(
            r"(?:шрифт|font)\s*[:—-]?\s*[«\"]?([A-Za-zА-Яа-яЁё ]{3,40})",
            text,
            re.IGNORECASE,
        )
        size_match = re.search(
            r"(?:кегль|размер(?:\s+шрифта)?|font\s*size)\s*[:—-]?\s*(\d+(?:[.,]\d+)?)",
            text,
            re.IGNORECASE,
        )
        margin_match = re.search(
            r"(?:поля|margin)\s*[:—-]?\s*(\d+(?:[.,]\d+)?)\s*(мм|см|mm|cm)",
            text,
            re.IGNORECASE,
        )
        columns = 2 if re.search(r"(?:две|2)[-\s]?колон", text, re.IGNORECASE) else 1
        margin = self._metric_mm(margin_match) if margin_match else 20.0
        requirements = [
            line.strip(" -*\t")
            for line in text.splitlines()
            if line.strip() and len(line.strip()) <= 240
        ][:100]
        return TemplateProfile(
            name=source.stem,
            source_path=str(source),
            source_type="requirements",
            confidence=0.55,
            page=PageLayout(
                margin_top_mm=margin,
                margin_right_mm=margin,
                margin_bottom_mm=margin,
                margin_left_mm=margin,
                columns=columns,
            ),
            typography=TypographyProfile(
                main_font=(font_match.group(1).strip(" «»\"") if font_match else "Times New Roman"),
                main_size_pt=(
                    float(size_match.group(1).replace(",", ".")) if size_match else 12.0
                ),
            ),
            requirements=requirements,
            evidence={
                "font_rule_found": bool(font_match),
                "size_rule_found": bool(size_match),
                "margin_rule_found": bool(margin_match),
            },
        )

    @staticmethod
    def _metric_mm(match: re.Match[str]) -> float:
        value = float(match.group(1).replace(",", "."))
        return value * 10 if match.group(2).lower() in {"см", "cm"} else value
