from pathlib import Path

from paper_formatter.models import (
    ArticleIR,
    ArticleMetadata,
    Author,
    EquationBlock,
    LocalizedText,
    SectionBlock,
    TableBlock,
)
from paper_formatter.parsers.latex_parser import LatexParser
from paper_formatter.renderers.latex_renderer import LatexRenderer
from paper_formatter.template_analyzers.latex_analyzer import LatexTemplateAnalyzer
from paper_formatter.validator import ConversionValidator


def test_latex_parser_preserves_structure_and_references(tmp_path: Path) -> None:
    source = tmp_path / "main.tex"
    source.write_text(
        r"""
\documentclass[10pt,twocolumn]{article}
\usepackage[margin=15mm]{geometry}
\setmainfont{TeX Gyre Pagella}
\title{Тестовая статья}
\author{Иван Иванов}
\begin{document}
\maketitle
\begin{abstract}Краткая аннотация.\end{abstract}
\section{Метод}
См. формулу~\eqref{eq:a} и источник~\cite{ref-a}.
\begin{equation}a+b=c\label{eq:a}\end{equation}
\begin{table}\caption{Данные}\begin{tabular}{cc}A&B\\1&2\end{tabular}\end{table}
\begin{thebibliography}{9}
\bibitem{ref-a} Автор. Название.
\end{thebibliography}
\end{document}
""".strip(),
        encoding="utf-8",
    )

    article = LatexParser(source, tmp_path / "assets").parse()

    assert article.metadata.titles[0].text == "Тестовая статья"
    assert any(isinstance(block, SectionBlock) for block in article.body)
    assert any(isinstance(block, EquationBlock) for block in article.body)
    assert any(isinstance(block, TableBlock) for block in article.body)
    assert article.citations[0].keys == ["ref-a"]
    assert article.cross_references[0].target_id == "eq:a"
    assert article.references[0].citation_key == "ref-a"


def test_latex_template_profile_reads_layout(tmp_path: Path) -> None:
    source = tmp_path / "template.tex"
    source.write_text(
        r"""
\documentclass[10pt,twocolumn,a4paper]{article}
\usepackage[left=18mm,right=17mm,top=15mm,bottom=20mm]{geometry}
\setmainfont{TeX Gyre Pagella}
\begin{document}\end{document}
""".strip(),
        encoding="utf-8",
    )

    profile = LatexTemplateAnalyzer().analyze(source)

    assert profile.latex.document_class == "article"
    assert profile.page.columns == 2
    assert profile.page.margin_left_mm == 18.0
    assert profile.page.margin_bottom_mm == 20.0
    assert profile.typography.main_font == "TeX Gyre Pagella"
    assert profile.typography.main_size_pt == 10.0


def test_inline_math_keeps_spaces_and_keywords_are_not_body(tmp_path: Path) -> None:
    source = tmp_path / "main.tex"
    source.write_text(
        r"""
\documentclass{article}
\title{Title}
\author{Alex Example}
\begin{document}
\maketitle
\noindent\textbf{Keywords:} alpha, beta
\section{Method}
where $x$ is input and $y$ is output.
\end{document}
""".strip(),
        encoding="utf-8",
    )

    article = LatexParser(source, tmp_path / "assets").parse()
    rendered_text = "".join(
        "".join(run.text if run.math_latex is None else f"${run.math_latex}$" for run in block.runs)
        for block in article.body
        if hasattr(block, "runs")
    )

    assert article.metadata.keywords == ["alpha", "beta"]
    assert "Keywords" not in rendered_text
    assert "where $x$ is input and $y$ is output." in rendered_text


def test_publisher_profile_preserves_exact_class_options(tmp_path: Path) -> None:
    template = tmp_path / "ieee.tex"
    template.write_text(
        r"""
\documentclass[conference]{IEEEtran}
\usepackage{graphicx}
\begin{document}\end{document}
""".strip(),
        encoding="utf-8",
    )
    profile = LatexTemplateAnalyzer().analyze(template)
    article = ArticleIR(
        metadata=ArticleMetadata(
            titles=[LocalizedText(language="en", text="Benchmark title")],
            authors=[
                Author(id="a1", name="Alex Example"),
                Author(id="a2", name="Maria Example"),
            ],
        )
    )

    main = LatexRenderer().render(article, tmp_path / "out", profile)
    main_text = main.read_text(encoding="utf-8")
    metadata = (main.parent / "metadata.tex").read_text(encoding="utf-8")

    assert profile.page.columns == 2
    assert profile.page.paper_size == "letterpaper"
    assert profile.evidence["template_family"] == "ieee"
    assert r"\documentclass[conference]{IEEEtran}" in main_text
    assert "12pt" not in main_text
    assert r"\usepackage{titlesec}" not in main_text
    assert r"\author{Alex Example, Maria Example}" in metadata


def test_cas_metadata_does_not_invent_empty_affiliation(tmp_path: Path) -> None:
    template = tmp_path / "cas.tex"
    template.write_text(
        r"\documentclass[a4paper,fleqn]{cas-sc}",
        encoding="utf-8",
    )
    profile = LatexTemplateAnalyzer().analyze(template)
    article = ArticleIR(
        metadata=ArticleMetadata(
            titles=[LocalizedText(language="en", text="Benchmark title")],
            authors=[Author(id="a1", name="Alex Example")],
        )
    )

    main = LatexRenderer().render(article, tmp_path / "out", profile)
    metadata = (main.parent / "metadata.tex").read_text(encoding="utf-8")

    assert r"\documentclass[a4paper,fleqn]{cas-sc}" in main.read_text(
        encoding="utf-8"
    )
    assert r"\author[]{Alex Example}" in metadata
    assert r"\affiliation" not in metadata


def test_validator_expands_tex_inputs_for_counts(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\input{body}\end{document}",
        encoding="utf-8",
    )
    (tmp_path / "body.tex").write_text(
        r"""
\section{Method}
\begin{equation}x=1\end{equation}
\begin{table}\begin{tabular}{c}A\end{tabular}\end{table}
\includegraphics{plot.png}
""".strip(),
        encoding="utf-8",
    )

    counts = ConversionValidator()._source_counts(tmp_path / "main.tex")

    assert counts["formulas"] == 1
    assert counts["tables"] == 1
    assert counts["drawings"] == 1
