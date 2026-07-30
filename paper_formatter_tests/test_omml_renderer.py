from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree

from paper_formatter.models import (
    ArticleIR,
    EquationBlock,
    ParagraphBlock,
    TextRun,
)
from paper_formatter.renderers.docx_renderer import DocxRenderer
from paper_formatter.renderers.omml_renderer import LatexToOmmlConverter


pytestmark = pytest.mark.skipif(
    not LatexToOmmlConverter.available(),
    reason="Для теста нативных формул нужен Microsoft MML2OMML.XSL.",
)


def test_latex_to_omml_builds_fraction_radical_and_subscript() -> None:
    formula = (
        r"\mathrm{Score}_{\mathrm{semantic}}(q,c)="
        r"\frac{|T(q)\cap T(c)|}{\max(1,\sqrt{|T(q)|\cdot|T(c)|})}"
    )

    root = LatexToOmmlConverter().convert(formula)

    assert etree.QName(root).localname == "oMath"
    assert root.xpath("count(.//*[local-name()='f'])") == 1
    assert root.xpath("count(.//*[local-name()='rad'])") == 1
    assert root.xpath("count(.//*[local-name()='sSub'])") >= 1


def test_docx_renderer_writes_editable_omml_not_latex_text(
    tmp_path: Path,
) -> None:
    article = ArticleIR(
        body=[
            ParagraphBlock(
                id="p1",
                runs=[
                    TextRun(text="Inline: "),
                    TextRun(math_latex=r"x_i=\frac{a}{b}"),
                ],
            ),
            EquationBlock(
                id="e1",
                latex=r"\sum_{i=1}^{n}x_i",
                display=True,
                number="1",
            ),
        ]
    )

    output = DocxRenderer().render(article, tmp_path / "math.docx")
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml")
    root = etree.fromstring(document_xml)

    assert root.xpath("count(.//*[local-name()='oMath'])") == 2
    assert b"\\frac" not in document_xml
    assert b"\\sum" not in document_xml
