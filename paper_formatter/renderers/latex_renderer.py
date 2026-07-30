from __future__ import annotations

import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from paper_formatter.models import (
    ArticleIR,
    Asset,
    EquationBlock,
    FigureBlock,
    ListItemBlock,
    ParagraphBlock,
    RawBlock,
    SectionBlock,
    TableBlock,
    TemplateProfile,
    TextRun,
)
from paper_formatter.utils.text import latex_break_long_tokens, latex_escape


_SUPPORTED_GRAPHICS = {".png", ".jpg", ".jpeg", ".pdf"}
_STANDARD_PACKAGES = [
    "iftex",
    "fontspec",
    "polyglossia",
    "geometry",
    "amsmath",
    "amssymb",
    "wasysym",
    "graphicx",
    "float",
    "placeins",
    "array",
    "ragged2e",
    "tabularx",
    "longtable",
    "booktabs",
    "microtype",
    "xurl",
    "titlesec",
    "titling",
    "enumitem",
    "hyperref",
    "textcomp",
]
_LATEX_TEMPLATE_PACKAGES = [
    "iftex",
    "graphicx",
    "amsmath",
    "array",
    "ragged2e",
    "tabularx",
    "longtable",
    "booktabs",
    "placeins",
    "xurl",
    "textcomp",
]


class LatexRenderer:
    def __init__(self) -> None:
        template_dir = Path(__file__).resolve().parent.parent / "templates"
        self.environment = Environment(
            loader=FileSystemLoader(template_dir),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        self.environment.filters["latex"] = latex_escape
        self._profile = TemplateProfile()

    def render(
        self,
        article: ArticleIR,
        output_dir: Path,
        profile: TemplateProfile | None = None,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        profile = profile or TemplateProfile()
        self._profile = profile
        self._install_vendor_files(output_dir, profile)
        assets_by_id = {asset.id: asset for asset in article.assets}
        latex_template = profile.source_type == "latex"
        class_name = profile.latex.document_class.lower()
        use_modern_fonts = not latex_template or bool(
            profile.evidence.get("main_font_found")
        )
        apply_geometry = not latex_template or (
            class_name == "article" and bool(profile.evidence.get("geometry_found"))
        )
        apply_typography = not latex_template
        metadata_in_document = class_name in {"cas-sc", "cas-dc"}
        preamble_commands = (
            [r"\setcitestyle{numbers}"]
            if class_name in {"cas-sc", "cas-dc"}
            else []
        )
        packages = self._packages(profile, use_modern_fonts=use_modern_fonts)
        if apply_geometry and "geometry" not in packages:
            packages.insert(1, "geometry")

        metadata_path = output_dir / "metadata.tex"
        metadata_path.write_text(
            self._render_metadata(article),
            encoding="utf-8",
        )
        body_path = output_dir / "body.tex"
        body_path.write_text(
            self._render_body(article, assets_by_id).rstrip() + "\n",
            encoding="utf-8",
        )

        template = self.environment.get_template("generic_article.tex.j2")
        content = template.render(
            document_class=self._safe_command(profile.latex.document_class, "article"),
            class_options=self._class_options(profile),
            packages=packages,
            main_font=self._safe_font(profile.typography.main_font),
            main_size_pt=profile.typography.main_size_pt,
            line_spacing=profile.typography.line_spacing,
            title_size_pt=profile.typography.title_size_pt,
            title_bold=profile.typography.title_bold,
            heading_commands=self._heading_commands(profile),
            page=profile.page,
            custom_latex=article.custom_latex,
            columns=profile.page.columns,
            use_modern_fonts=use_modern_fonts,
            apply_geometry=apply_geometry,
            apply_typography=apply_typography,
            metadata_in_document=metadata_in_document,
            preamble_commands=preamble_commands,
            title_zone=(
                apply_geometry
                and profile.page.title_margin_left_mm is not None
                and profile.page.title_margin_right_mm is not None
                and profile.page.width_mm is not None
            ),
            title_left_shift_mm=(
                (profile.page.title_margin_left_mm or profile.page.margin_left_mm)
                - profile.page.margin_left_mm
            ),
            title_width_mm=(
                (profile.page.width_mm or 210.0)
                - (profile.page.title_margin_left_mm or profile.page.margin_left_mm)
                - (profile.page.title_margin_right_mm or profile.page.margin_right_mm)
            ),
        )
        main_tex = output_dir / "main.tex"
        main_tex.write_text(content.rstrip() + "\n", encoding="utf-8")
        return main_tex

    @staticmethod
    def _install_vendor_files(output_dir: Path, profile: TemplateProfile) -> None:
        if profile.latex.document_class.lower() != "jacow":
            return
        source = (
            Path(__file__).resolve().parent.parent
            / "vendor"
            / "latex"
            / "jacow.cls"
        )
        if source.exists():
            shutil.copy2(source, output_dir / "jacow.cls")
        font_source = (
            Path(__file__).resolve().parent.parent / "vendor" / "fonts"
        )
        font_target = output_dir / "fonts"
        font_target.mkdir(parents=True, exist_ok=True)
        for font in font_source.glob("*.otf"):
            shutil.copy2(font, font_target / font.name)
        config = font_source / "fonts.conf"
        if config.exists():
            shutil.copy2(config, output_dir / "fonts.conf")
        license_path = font_source / "GUST-FONT-LICENSE.txt"
        if license_path.exists():
            shutil.copy2(license_path, font_target / license_path.name)

    def _render_metadata(self, article: ArticleIR) -> str:
        title = article.metadata.titles[0].text if article.metadata.titles else "Без названия"
        subtitle = article.metadata.subtitles[0].text if article.metadata.subtitles else ""
        full_title = title + (f": {subtitle}" if subtitle else "")
        title_tex = latex_escape(full_title)
        generic_title_tex = latex_escape(title)
        if subtitle:
            generic_title_tex += (
                r"\\[0.75em]{\normalsize\normalfont\itshape "
                + latex_escape(subtitle)
                + "}"
            )
        authors = [latex_escape(author.name) for author in article.metadata.authors]
        affiliations = [latex_escape(item.name) for item in article.metadata.affiliations]
        authors_and = r" \and ".join(authors)
        generic_authors = (
            ", ".join(authors)
            if (
                self._profile.page.title_margin_left_mm is not None
                and self._profile.page.title_margin_right_mm is not None
            )
            else authors_and
        )
        affiliations_break = r" \\ ".join(affiliations)
        abstract = "\n\n".join(
            latex_escape(item.text) for item in article.metadata.abstracts
        )
        keywords = [latex_escape(item) for item in article.metadata.keywords]
        class_name = self._profile.latex.document_class.lower()

        if class_name == "ieeetran":
            lines = [
                rf"\title{{{title_tex}}}",
                rf"\author{{{', '.join(authors)}}}" if authors else r"\author{}",
                r"\newcommand{\PFMakeMetadata}{%",
                r"  \maketitle",
            ]
            self._append_abstract(lines, abstract, indent="  ")
            if keywords:
                lines.extend(
                    [
                        r"  \begin{IEEEkeywords}",
                        ", ".join(keywords),
                        r"  \end{IEEEkeywords}",
                    ]
                )
            lines.append("}")
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "acmart":
            lines = [rf"\title{{{title_tex}}}"]
            for author in authors or [""]:
                lines.append(rf"\author{{{author}}}")
                for affiliation in affiliations[:1]:
                    lines.append(
                        rf"\affiliation{{\institution{{{affiliation}}}}}"
                    )
            self._append_abstract(lines, abstract)
            if keywords:
                lines.append(rf"\keywords{{{', '.join(keywords)}}}")
            lines.extend(
                [r"\newcommand{\PFMakeMetadata}{%", r"  \maketitle", "}"]
            )
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "elsarticle":
            lines = [r"\newcommand{\PFMakeMetadata}{%", r"  \begin{frontmatter}"]
            lines.append(rf"  \title{{{title_tex}}}")
            for author in authors or [""]:
                lines.append(rf"  \author{{{author}}}")
            for index, affiliation in enumerate(affiliations, start=1):
                lines.append(rf"  \address{{{affiliation}}}")
            self._append_abstract(lines, abstract, indent="  ")
            if keywords:
                lines.extend(
                    [
                        r"  \begin{keyword}",
                        r" \sep ".join(keywords),
                        r"  \end{keyword}",
                    ]
                )
            lines.extend([r"  \end{frontmatter}", "}"])
            return "\n".join(lines).rstrip() + "\n"

        if class_name in {"cas-sc", "cas-dc"}:
            short_title = latex_escape(self._short_text(full_title, 55))
            short_authors = latex_escape(self._short_authors(article))
            lines = [
                rf"\shorttitle{{{short_title}}}",
                rf"\shortauthors{{{short_authors}}}",
                rf"\title[mode=title]{{{title_tex}}}",
            ]
            for author in authors or [""]:
                marker = "1" if affiliations else ""
                lines.append(rf"\author[{marker}]{{{author}}}")
            if affiliations:
                lines.append(
                    rf"\affiliation[1]{{organization={{{affiliations[0]}}}}}"
                )
            self._append_abstract(lines, abstract)
            if keywords:
                lines.extend(
                    [
                        r"\begin{keywords}",
                        r" \sep ".join(keywords),
                        r"\end{keywords}",
                    ]
                )
            lines.append(r"\maketitle")
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "llncs":
            lines = [
                r"\newcommand{\PFMakeMetadata}{%",
                rf"  \title{{{title_tex}}}",
                rf"  \author{{{authors_and}}}"
                if authors
                else r"  \author{}",
                rf"  \institute{{{affiliations_break}}}"
                if affiliations
                else r"  \institute{}",
                r"  \maketitle",
            ]
            self._append_abstract(lines, abstract, indent="  ", close=False)
            if abstract:
                if keywords:
                    lines.append(rf"  \keywords{{{', '.join(keywords)}}}")
                lines.append(r"  \end{abstract}")
            lines.append("}")
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "revtex4-2":
            lines = [r"\newcommand{\PFMakeMetadata}{%", rf"  \title{{{title_tex}}}"]
            for author in authors or [""]:
                lines.append(rf"  \author{{{author}}}")
                for affiliation in affiliations[:1]:
                    lines.append(rf"  \affiliation{{{affiliation}}}")
            self._append_abstract(lines, abstract, indent="  ")
            lines.extend([r"  \maketitle", "}"])
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "aastex631":
            lines = [r"\newcommand{\PFMakeMetadata}{%", rf"  \title{{{title_tex}}}"]
            for author in authors or [""]:
                lines.append(rf"  \author{{{author}}}")
                for affiliation in affiliations[:1]:
                    lines.append(rf"  \affiliation{{{affiliation}}}")
            self._append_abstract(lines, abstract, indent="  ")
            if keywords:
                lines.append(rf"  \keywords{{{', '.join(keywords)}}}")
            lines.append("}")
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "mnras":
            short_title = latex_escape(self._short_text(full_title, 58))
            short_authors = latex_escape(self._short_authors(article))
            author_text = " and ".join(authors)
            lines = [
                rf"\title[{short_title}]{{{title_tex}}}",
                rf"\author[{short_authors}]{{{author_text}}}"
                if authors
                else r"\author{}",
                r"\date{}",
                r"\newcommand{\PFMakeMetadata}{%",
                r"  \maketitle",
            ]
            self._append_abstract(lines, abstract, indent="  ")
            if keywords:
                lines.extend(
                    [
                        r"  \begin{keywords}",
                        ", ".join(keywords),
                        r"  \end{keywords}",
                    ]
                )
            lines.append("}")
            return "\n".join(lines).rstrip() + "\n"

        if class_name == "jacow":
            lines = [
                r"\newcommand{\PFMakeMetadata}{%",
                rf"  \title{{{title_tex}}}",
                rf"  \author{{{', '.join(authors)}}}" if authors else r"  \author{}",
                r"  \maketitle",
            ]
            self._append_abstract(lines, abstract, indent="  ")
            lines.append("}")
            return "\n".join(lines).rstrip() + "\n"

        lines = [
            rf"\title{{{generic_title_tex}}}",
            rf"\author{{{generic_authors}}}" if authors else r"\author{}",
            r"\date{}",
            r"\newcommand{\PFMakeMetadata}{%",
            r"  \maketitle",
        ]
        if affiliations:
            lines.append(
                r"  \begin{center}\small "
                + r"\\ ".join(affiliations)
                + r"\end{center}"
            )
        if article.metadata.udc:
            lines.append(
                rf"  \noindent\textbf{{УДК:}} {latex_escape(article.metadata.udc)}\par\medskip"
            )
        if article.metadata.doi:
            lines.append(
                rf"  \noindent\textbf{{DOI:}} {latex_escape(article.metadata.doi)}\par\medskip"
            )
        self._append_abstract(lines, abstract, indent="  ")
        if keywords:
            label = (
                "Ключевые слова"
                if self._document_language(article) == "ru"
                else "Keywords"
            )
            lines.append(
                rf"  \noindent\textbf{{{label}:}} "
                + ", ".join(keywords)
                + r".\par\medskip"
            )
        lines.append("}")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _append_abstract(
        lines: list[str],
        abstract: str,
        *,
        indent: str = "",
        close: bool = True,
    ) -> None:
        if not abstract:
            return
        lines.extend([f"{indent}\\begin{{abstract}}", abstract])
        if close:
            lines.append(f"{indent}\\end{{abstract}}")

    @staticmethod
    def _short_text(value: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) <= limit:
            return value
        shortened = value[: max(1, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,:;-")
        return shortened or value[: max(1, limit - 1)]

    @staticmethod
    def _short_authors(article: ArticleIR) -> str:
        names = [author.name for author in article.metadata.authors]
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        first = names[0].split()[-1]
        second = names[1].split()[-1]
        return f"{first} and {second}" if len(names) == 2 else f"{first} et al."

    @staticmethod
    def _document_language(article: ArticleIR) -> str:
        languages = [
            item.language
            for item in [*article.metadata.titles, *article.metadata.abstracts]
            if item.language
        ]
        if languages:
            return "ru" if languages.count("ru") > languages.count("en") else "en"
        text = " ".join(item.text for item in article.metadata.titles)
        return "ru" if re.search(r"[А-Яа-яЁё]", text) else "en"

    def _render_runs(self, runs: list[TextRun], assets_by_id: dict[str, Asset]) -> str:
        parts: list[str] = []
        for run in runs:
            if run.asset_id:
                parts.append(self._render_formula_image(run, assets_by_id, inline=True))
                continue
            if run.math_latex is not None:
                parts.append(rf"\({self._math_latex(run.math_latex)}\)")
                continue
            if run.citation_keys:
                keys = ",".join(self._safe_label(key) for key in run.citation_keys)
                parts.append(rf"\cite{{{keys}}}")
                continue
            if run.reference_target:
                parts.append(rf"\ref{{{self._safe_label(run.reference_target)}}}")
                continue

            value = latex_escape(run.text)
            value = value.replace("\n", r"\\ ")
            if run.hyperlink:
                value = rf"\href{{{latex_escape(run.hyperlink)}}}{{{value}}}"
            if run.superscript:
                value = rf"\textsuperscript{{{value}}}"
            if run.subscript:
                value = rf"\textsubscript{{{value}}}"
            if run.underline:
                value = rf"\underline{{{value}}}"
            if run.italic:
                value = rf"\textit{{{value}}}"
            if run.bold:
                value = rf"\textbf{{{value}}}"
            parts.append(value)
        return latex_break_long_tokens("".join(parts))

    def _render_formula_image(
        self,
        run: TextRun,
        assets_by_id: dict[str, Asset],
        inline: bool,
    ) -> str:
        asset = assets_by_id.get(run.asset_id or "")
        if asset is None:
            return r"\fbox{\texttt{[формула не найдена]}}"
        if Path(asset.path).suffix.lower() not in _SUPPORTED_GRAPHICS:
            return r"\fbox{\texttt{[формула MathType]}}"

        path = asset.path.replace("\\", "/")
        if inline:
            height = max(8.0, min(run.height_pt or 13.0, 28.0))
            return (
                rf"\raisebox{{-0.25\height}}{{"
                rf"\includegraphics[height={height:.2f}pt,keepaspectratio]{{{path}}}}}"
            )
        if run.width_pt:
            width = max(10.0, min(run.width_pt, 430.0))
            return rf"\includegraphics[width={width:.2f}pt,keepaspectratio]{{{path}}}"
        height = max(12.0, min(run.height_pt or 24.0, 160.0))
        return rf"\includegraphics[height={height:.2f}pt,keepaspectratio]{{{path}}}"

    def _render_paragraph(self, block: ParagraphBlock, assets_by_id: dict[str, Asset]) -> str:
        meaningful = [
            run for run in block.runs if run.text or run.asset_id or run.math_latex
        ]
        if meaningful and all(run.asset_id and run.formula_image for run in meaningful):
            images = [
                self._render_formula_image(run, assets_by_id, inline=False)
                for run in meaningful
            ]
            return "\n".join([r"\begin{center}", *images, r"\end{center}"])
        return self._render_runs(block.runs, assets_by_id)

    def _render_body(self, article: ArticleIR, assets_by_id: dict[str, Asset]) -> str:
        lines: list[str] = []
        list_stack: list[bool] = []

        def close_lists(target_depth: int = 0) -> None:
            while len(list_stack) > target_depth:
                ordered = list_stack.pop()
                lines.append(r"\end{enumerate}" if ordered else r"\end{itemize}")
                lines.append("")

        for block in article.body:
            if isinstance(block, ListItemBlock):
                target_depth = max(1, block.level + 1)
                while len(list_stack) > target_depth:
                    close_lists(len(list_stack) - 1)
                if len(list_stack) == target_depth and list_stack[-1] != block.ordered:
                    close_lists(target_depth - 1)
                while len(list_stack) < target_depth:
                    lines.append(r"\begin{enumerate}" if block.ordered else r"\begin{itemize}")
                    list_stack.append(block.ordered)
                lines.append(r"\item " + self._render_runs(block.runs, assets_by_id))
                continue

            close_lists()
            if isinstance(block, SectionBlock):
                command = {
                    1: "section",
                    2: "subsection",
                    3: "subsubsection",
                    4: "paragraph",
                    5: "subparagraph",
                    6: "subparagraph",
                }[block.level]
                label = rf"\label{{{self._safe_label(block.id)}}}"
                lines.extend(
                    [rf"\{command}{{{latex_escape(block.title)}}}{label}", ""]
                )
            elif isinstance(block, ParagraphBlock):
                lines.extend([self._render_paragraph(block, assets_by_id), ""])
            elif isinstance(block, EquationBlock):
                if block.display:
                    label = self._safe_label(block.label or block.id)
                    math_lines = self._math_lines(self._math_latex(block.latex))
                    environment = "multline" if len(math_lines) > 1 else "equation"
                    lines.append(rf"\begin{{{environment}}}")
                    for index, math_line in enumerate(math_lines):
                        suffix = r"\\" if index + 1 < len(math_lines) else ""
                        if (
                            self._profile.page.columns > 1
                            and len(math_lines) == 1
                            and len(math_line) > 120
                        ):
                            lines.append(
                                r"\resizebox{0.98\columnwidth}{!}{$\displaystyle "
                                + math_line
                                + r"$}"
                            )
                        else:
                            lines.append(math_line + suffix)
                    lines.extend(
                        [
                            rf"\label{{{label}}}",
                            rf"\end{{{environment}}}",
                            "",
                        ]
                    )
                else:
                    lines.extend([rf"\({self._math_latex(block.latex)}\)", ""])
            elif isinstance(block, FigureBlock):
                lines.extend(self._render_figure(block, assets_by_id))
            elif isinstance(block, TableBlock):
                lines.extend(self._render_table(block))
            elif isinstance(block, RawBlock):
                if block.format.lower() == "latex":
                    lines.extend([block.content, ""])
                else:
                    lines.extend(
                        [
                            r"\begin{verbatim}",
                            block.content,
                            r"\end{verbatim}",
                            "",
                        ]
                    )

        close_lists()
        if article.references:
            lines.extend(
                [
                    r"\FloatBarrier",
                    r"\begin{thebibliography}{99}",
                    r"\small",
                ]
            )
            for index, reference in enumerate(article.references, start=1):
                key = self._safe_label(reference.citation_key or f"ref{index}")
                if self._profile.latex.document_class.lower() in {
                    "aastex631",
                    "mnras",
                }:
                    natbib_label = self._natbib_label(reference.text, key)
                    lines.append(
                        rf"\bibitem[{natbib_label}]{{{key}}} "
                        + latex_escape(reference.text)
                    )
                else:
                    lines.append(rf"\bibitem{{{key}}} {latex_escape(reference.text)}")
            lines.extend([r"\end{thebibliography}", ""])
        return "\n".join(lines).rstrip()

    def _render_figure(
        self, block: FigureBlock, assets_by_id: dict[str, Asset]
    ) -> list[str]:
        asset = assets_by_id.get(block.asset_id)
        if asset is None:
            return [r"\fbox{\texttt{[рисунок не найден]}}", ""]
        if Path(asset.path).suffix.lower() not in _SUPPORTED_GRAPHICS:
            return [r"\fbox{\texttt{[рисунок неподдерживаемого формата]}}", ""]
        path = asset.path.replace("\\", "/")
        width = (
            rf"{max(20.0, min(block.width_pt, 430.0)):.2f}pt"
            if block.width_pt
            else r"0.9\linewidth"
        )
        lines = [
            rf"\begin{{figure}}[{block.placement or 'htbp'}]",
            r"\centering",
            rf"\includegraphics[width={width},keepaspectratio]{{{path}}}",
        ]
        if block.caption:
            lines.append(rf"\caption{{{latex_escape(block.caption)}}}")
        lines.append(rf"\label{{{self._safe_label(block.label or block.id)}}}")
        lines.extend([r"\end{figure}", ""])
        return lines

    def _render_table(self, block: TableBlock) -> list[str]:
        if not block.rows:
            return []
        columns = max(len(row) for row in block.rows)
        weights, numeric_columns = self._table_column_layout(block, columns)
        label = self._safe_label(block.label or block.id)
        if len(block.rows) > 35 and self._profile.page.columns == 1:
            alignment = "".join(
                (
                    r">{\centering\arraybackslash}p{"
                    if numeric_columns[index]
                    else r">{\RaggedRight\arraybackslash}p{"
                )
                + f"{0.94 * weights[index]:.3f}"
                + r"\linewidth}"
                for index in range(columns)
            )
            font_size, tab_sep = self._table_sizing(columns)
            lines = ["{", font_size, rf"\setlength{{\tabcolsep}}{{{tab_sep}pt}}", rf"\begin{{longtable}}{{{alignment}}}"]
            if block.caption:
                lines.append(rf"\caption{{{latex_escape(block.caption)}}}\label{{{label}}}\\")
            else:
                lines.append(rf"\label{{{label}}}\\")
            lines.append(r"\toprule")
            for row_index, row in enumerate(block.rows):
                values = [latex_escape(value) for value in row] + [""] * (
                    columns - len(row)
                )
                if row_index < block.header_rows:
                    values = [
                        rf"{{\RaggedRight\arraybackslash\textbf{{{value}}}}}"
                        for value in values
                    ]
                lines.append(" & ".join(values) + r" \\")
                if row_index + 1 == max(1, block.header_rows):
                    lines.append(r"\midrule")
            lines.extend([r"\bottomrule", r"\end{longtable}", "}", ""])
            return lines

        alignment = "".join(
            (
                r">{\hsize="
                + f"{weights[index] * columns:.3f}"
                + r"\hsize"
                + (
                    r"\centering\arraybackslash}X"
                    if numeric_columns[index]
                    else r"\RaggedRight\arraybackslash}X"
                )
            )
            for index in range(columns)
        )
        font_size, tab_sep = self._table_sizing(columns)
        environment = "table*" if self._profile.page.columns > 1 and columns >= 4 else "table"
        lines = [rf"\begin{{{environment}}}[htbp]", r"\centering", font_size, rf"\setlength{{\tabcolsep}}{{{tab_sep}pt}}"]
        if block.caption:
            lines.append(rf"\caption{{{latex_escape(block.caption)}}}")
        lines.append(rf"\label{{{label}}}")
        lines.extend([rf"\begin{{tabularx}}{{\linewidth}}{{{alignment}}}", r"\toprule"])
        for row_index, row in enumerate(block.rows):
            values = [latex_escape(value) for value in row] + [""] * (
                columns - len(row)
            )
            if row_index < block.header_rows:
                values = [
                    rf"{{\RaggedRight\arraybackslash\textbf{{{value}}}}}"
                    for value in values
                ]
            lines.append(" & ".join(values) + r" \\")
            if row_index + 1 == max(1, block.header_rows):
                lines.append(r"\midrule")
        lines.extend([r"\bottomrule", r"\end{tabularx}", rf"\end{{{environment}}}", ""])
        return lines

    @staticmethod
    def _table_column_layout(
        block: TableBlock,
        columns: int,
    ) -> tuple[list[float], list[bool]]:
        raw: list[float] = []
        numeric_columns: list[bool] = []
        for column in range(columns):
            values = [
                row[column].strip()
                for row in block.rows
                if column < len(row) and row[column].strip()
            ]
            body_values = values[block.header_rows :] or values
            numeric_share = (
                sum(LatexRenderer._looks_numeric(value) for value in body_values)
                / len(body_values)
                if body_values
                else 0.0
            )
            numeric = numeric_share >= 0.6
            numeric_columns.append(numeric)
            longest = max((len(value) for value in values), default=6)
            longest_word = max(
                (
                    len(word)
                    for value in values
                    for word in re.findall(r"[\w-]+", value, flags=re.UNICODE)
                ),
                default=6,
            )
            content_weight = (
                max(7.0, min(longest, 14) * 0.65)
                if numeric
                else max(8.0, min(longest, 40) * 0.75)
            )
            raw.append(max(content_weight, longest_word * 0.8))
        total = sum(raw) or float(columns)
        factors = [value * columns / total for value in raw]
        minimum = 0.75
        fixed: set[int] = set()
        while True:
            new_fixed = {
                index
                for index, factor in enumerate(factors)
                if factor < minimum and index not in fixed
            }
            if not new_fixed:
                break
            fixed.update(new_fixed)
            remaining = columns - minimum * len(fixed)
            flexible = [index for index in range(columns) if index not in fixed]
            flexible_weight = sum(raw[index] for index in flexible)
            for index in fixed:
                factors[index] = minimum
            for index in flexible:
                factors[index] = (
                    remaining * raw[index] / flexible_weight
                    if flexible_weight
                    else remaining / max(1, len(flexible))
                )
        return [factor / columns for factor in factors], numeric_columns

    @staticmethod
    def _looks_numeric(value: str) -> bool:
        return bool(
            value
            and re.fullmatch(
                r"[\s+\-−±]?(?:\d+(?:[.,]\d+)?|[–—-])(?:\s*[%°])?",
                value,
            )
        )

    @staticmethod
    def _class_options(profile: TemplateProfile) -> list[str]:
        options = list(profile.latex.class_options)
        if profile.source_type == "latex":
            return list(dict.fromkeys(options))
        if not any(re.fullmatch(r"\d+(?:\.\d+)?pt", item) for item in options):
            options.insert(0, f"{max(8, round(profile.typography.main_size_pt))}pt")
        if profile.page.paper_size and profile.page.paper_size not in options:
            options.append(profile.page.paper_size)
        if profile.page.columns == 2 and "twocolumn" not in options:
            options.append("twocolumn")
        return list(dict.fromkeys(options))

    @staticmethod
    def _packages(
        profile: TemplateProfile, *, use_modern_fonts: bool
    ) -> list[str]:
        blocked = {
            "inputenc",
            "fontenc",
            "babel",
            "geometry",
            "fontspec",
            "polyglossia",
        }
        if profile.latex.document_class.lower() == "acmart":
            blocked.add("amssymb")
        custom = [
            package
            for package in profile.latex.packages
            if package.lower() not in blocked
            and re.fullmatch(r"[A-Za-z0-9_.-]+", package)
        ]
        base = (
            _LATEX_TEMPLATE_PACKAGES
            if profile.source_type == "latex"
            else _STANDARD_PACKAGES
        )
        packages = [*base, *custom]
        if profile.latex.document_class.lower() in {"cas-sc", "cas-dc"}:
            packages.append("natbib")
        if use_modern_fonts:
            for package in ("fontspec", "polyglossia"):
                if package not in packages:
                    packages.insert(1, package)
        if profile.source_type != "latex" and "geometry" not in packages:
            packages.insert(3, "geometry")
        return list(dict.fromkeys(packages))

    @staticmethod
    def _safe_command(value: str, fallback: str) -> str:
        return value if re.fullmatch(r"[A-Za-z@][A-Za-z0-9@_.-]*", value) else fallback

    @staticmethod
    def _safe_font(value: str | None) -> str:
        value = (value or "Times New Roman").strip()
        return (
            value
            if re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё ._()+-]{1,100}", value)
            else "Times New Roman"
        )

    def _heading_commands(self, profile: TemplateProfile) -> list[str]:
        if profile.source_type == "latex":
            return []
        names = {
            1: "section",
            2: "subsection",
            3: "subsubsection",
            4: "paragraph",
            5: "subparagraph",
            6: "subparagraph",
        }
        commands: list[str] = []
        for heading in profile.headings:
            name = names[heading.level]
            style: list[str] = []
            if heading.alignment == "center":
                style.append(r"\centering")
            elif heading.alignment == "right":
                style.append(r"\raggedleft")
            if heading.size_pt:
                baseline = max(heading.size_pt + 1, heading.size_pt * 1.18)
                style.append(
                    rf"\fontsize{{{heading.size_pt:.2f}}}{{{baseline:.2f}}}\selectfont"
                )
            if heading.bold:
                style.append(r"\bfseries")
            if heading.italic:
                style.append(r"\itshape")
            if heading.font:
                style.append(rf"\fontspec{{{self._safe_font(heading.font)}}}")
            label = (
                ""
                if heading.numbered is False
                else {
                    1: r"\thesection",
                    2: r"\thesubsection",
                    3: r"\thesubsubsection",
                    4: "",
                    5: "",
                    6: "",
                }[heading.level]
            )
            commands.append(
                rf"\titleformat{{\{name}}}{{{' '.join(style)}}}{{{label}}}"
                rf"{{{1 if label else 0}em}}{{}}"
            )
            before = heading.space_before_pt if heading.space_before_pt is not None else 10.0
            after = heading.space_after_pt if heading.space_after_pt is not None else 4.0
            commands.append(
                rf"\titlespacing*{{\{name}}}{{0pt}}{{{before:.2f}pt}}{{{after:.2f}pt}}"
            )
        return commands

    @staticmethod
    def _table_sizing(columns: int) -> tuple[str, float]:
        if columns >= 6:
            return r"\tiny", 1.0
        return r"\footnotesize", 3.0

    @staticmethod
    def _natbib_label(text: str, fallback: str) -> str:
        year_match = re.search(r"\b((?:19|20)\d{2})\b", text)
        author_part = text.split(",", 1)[0]
        surnames = re.findall(r"\b[A-Z][A-Za-z'-]{2,}\b", author_part)
        surnames = [
            value
            for value in surnames
            if value.lower() not in {"and", "the", "et", "al"}
        ]
        if not surnames:
            author = fallback
        elif len(surnames) == 1:
            author = surnames[0]
        elif len(surnames) == 2:
            author = f"{surnames[0]} and {surnames[1]}"
        else:
            author = f"{surnames[0]} et al."
        year = year_match.group(1) if year_match else "n.d."
        return f"{author}({year})"

    @staticmethod
    def _math_lines(latex: str, target: int = 105) -> list[str]:
        if len(latex) <= target:
            return [latex]
        candidates: list[int] = []
        brace_depth = 0
        delimiter_depth = 0
        index = 0
        while index < len(latex):
            if latex.startswith(r"\left", index):
                delimiter_depth += 1
                index += len(r"\left")
                continue
            if latex.startswith(r"\right", index):
                delimiter_depth = max(0, delimiter_depth - 1)
                index += len(r"\right")
                continue
            char = latex[index]
            if char == "\\":
                index += 2
                continue
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif brace_depth == 0 and delimiter_depth == 0 and char in {"+", "="}:
                candidates.append(index)
            index += 1
        if not candidates:
            return [latex]
        lines: list[str] = []
        start = 0
        while len(latex) - start > target:
            desired = start + target
            available = [index for index in candidates if start + 30 <= index <= desired + 25]
            if not available:
                break
            split = min(available, key=lambda value: abs(value - desired))
            lines.append(latex[start:split].rstrip())
            start = split
        lines.append(latex[start:].lstrip())
        return [line for line in lines if line]

    @staticmethod
    def _math_latex(latex: str) -> str:
        replacements = {
            "⌀": r"\diameter",
            "∧": r"\land ",
            "∨": r"\lor ",
            "¬": r"\neg ",
            "∈": r"\in ",
            "∉": r"\notin ",
            "≤": r"\le ",
            "≥": r"\ge ",
            "≠": r"\ne ",
            "×": r"\times ",
            "·": r"\cdot ",
            "→": r"\to ",
            "∞": r"\infty ",
        }
        return "".join(replacements.get(char, char) for char in latex)

    @staticmethod
    def _safe_label(value: str) -> str:
        value = re.sub(r"[^A-Za-z0-9:_.-]+", "-", value.strip())
        return value.strip("-") or "item"
