from __future__ import annotations

import re
from pathlib import Path

from paper_formatter.exceptions import TemplateAnalysisError
from paper_formatter.models import (
    LatexTemplateProfile,
    PageLayout,
    TemplateProfile,
    TypographyProfile,
)
from paper_formatter.template_analyzers.base import TemplateAnalyzer


class LatexTemplateAnalyzer(TemplateAnalyzer):
    def analyze(self, source: Path) -> TemplateProfile:
        source = Path(source).resolve()
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise TemplateAnalysisError(f"Не удалось прочитать TEX-образец: {exc}") from exc

        preamble = text.split(r"\begin{document}", 1)[0]
        class_match = re.search(
            r"\\documentclass(?:\[([^\]]*)\])?\s*\{([^}]+)\}", preamble
        )
        class_options = self._csv(class_match.group(1)) if class_match else ["12pt"]
        document_class = class_match.group(2).strip() if class_match else "article"
        packages: list[str] = []
        for options, names in re.findall(
            r"\\usepackage(?:\[([^\]]*)\])?\s*\{([^}]+)\}", preamble
        ):
            packages.extend(self._csv(names))

        font_match = re.search(r"\\setmainfont(?:\[[^\]]*\])?\s*\{([^}]+)\}", preamble)
        font = font_match.group(1).strip() if font_match else "Times New Roman"
        size_pt = self._class_size(class_options)
        page = self._page_layout(preamble, class_options, document_class)
        bibliography_backend = "thebibliography"
        if re.search(r"\\addbibresource|\\usepackage(?:\[[^\]]*\])?\{biblatex\}", preamble):
            bibliography_backend = "biblatex"
        elif re.search(r"\\bibliographystyle|\\bibliography", text):
            bibliography_backend = "bibtex"
        style_match = re.search(r"\\bibliographystyle\s*\{([^}]+)\}", text)

        warnings: list[str] = []
        if r"\begin{document}" not in text:
            warnings.append("В TEX-образце не найдено \\begin{document}.")
        return TemplateProfile(
            name=source.stem,
            source_path=str(source),
            source_type="latex",
            confidence=0.92 if class_match else 0.65,
            page=page,
            typography=TypographyProfile(main_font=font, main_size_pt=size_pt),
            latex=LatexTemplateProfile(
                document_class=document_class,
                class_options=class_options,
                packages=list(dict.fromkeys(packages)),
                preamble=preamble.strip(),
                bibliography_backend=bibliography_backend,
                bibliography_style=style_match.group(1).strip() if style_match else None,
                source_main_file=str(source),
            ),
            evidence={
                "document_class_found": bool(class_match),
                "package_count": len(packages),
                "preamble_characters": len(preamble),
                "main_font_found": bool(font_match),
                "geometry_found": bool(
                    re.search(
                        r"\\usepackage(?:\[[^\]]*\])?\s*\{geometry\}|\\geometry\s*\{",
                        preamble,
                    )
                ),
                "template_family": self._template_family(document_class),
            },
            warnings=warnings,
        )

    def _page_layout(
        self, preamble: str, options: list[str], document_class: str
    ) -> PageLayout:
        class_name = document_class.lower()
        two_column_classes = {
            "ieeetran",
            "cas-dc",
            "aastex631",
        }
        two_column_options = {
            "twocolumn",
            "conference",
            "journal",
            "sigconf",
            "sigplan",
            "5p",
            "reprint",
        }
        columns = (
            2
            if class_name in two_column_classes
            or any(option.lower() in two_column_options for option in options)
            or r"\twocolumn" in preamble
            else 1
        )
        letter_classes = {"ieeetran", "acmart", "revtex4-2", "aastex631"}
        paper_size = (
            "letterpaper"
            if "letterpaper" in options or class_name in letter_classes
            else "a4paper"
        )
        values = {
            key.lower(): self._length_mm(value)
            for key, value in re.findall(
                r"(top|right|bottom|left|margin)\s*=\s*([0-9.]+\s*(?:mm|cm|in|pt))",
                preamble,
                re.IGNORECASE,
            )
        }
        common = values.get("margin", 20.0)
        return PageLayout(
            paper_size=paper_size,
            margin_top_mm=values.get("top", common),
            margin_right_mm=values.get("right", common),
            margin_bottom_mm=values.get("bottom", common),
            margin_left_mm=values.get("left", common),
            columns=columns,
        )

    @staticmethod
    def _template_family(document_class: str) -> str:
        class_name = document_class.lower()
        if class_name == "ieeetran":
            return "ieee"
        if class_name == "acmart":
            return "acm"
        if class_name == "elsarticle":
            return "elsevier"
        if class_name in {"cas-sc", "cas-dc"}:
            return "elsevier-cas"
        if class_name == "llncs":
            return "springer-lncs"
        if class_name == "revtex4-2":
            return "revtex"
        if class_name == "aastex631":
            return "aas"
        if class_name == "mnras":
            return "mnras"
        if class_name == "jacow":
            return "jacow"
        return "standard"

    @staticmethod
    def _class_size(options: list[str]) -> float:
        for value in options:
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)pt", value)
            if match:
                return float(match.group(1))
        return 12.0

    @staticmethod
    def _csv(value: str | None) -> list[str]:
        return [item.strip() for item in (value or "").split(",") if item.strip()]

    @staticmethod
    def _length_mm(value: str) -> float:
        match = re.fullmatch(r"\s*([0-9.]+)\s*(mm|cm|in|pt)\s*", value)
        if not match:
            return 20.0
        number = float(match.group(1))
        return round(
            number
            * {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.27}[match.group(2)],
            2,
        )
