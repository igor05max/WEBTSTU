from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement

from paper_formatter.models import (
    EquationBlock,
    ListItemBlock,
    ParagraphBlock,
    SectionBlock,
    TableBlock,
)
from paper_formatter.parsers.docx_parser import DocxParser
from paper_formatter.validator import ConversionValidator


def test_docx_parser_extracts_basic_structure(tmp_path: Path) -> None:
    source = tmp_path / "article.docx"
    document = Document()
    document.add_heading("Тестовая научная статья", level=0)
    paragraph = document.add_paragraph("Иванов И. И.")
    paragraph.style = document.styles["Subtitle"]
    document.add_paragraph("Аннотация: Проверка преобразования DOCX.")
    document.add_paragraph("Ключевые слова: DOCX; LaTeX; статья")
    document.add_heading("Введение", level=1)
    document.add_paragraph("Основной текст статьи.")
    document.add_paragraph("Пункт списка", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Параметр"
    table.cell(0, 1).text = "Значение"
    table.cell(1, 0).text = "A"
    table.cell(1, 1).text = "10"
    document.save(source)

    article = DocxParser(source, tmp_path / "assets").parse()

    assert article.metadata.titles[0].text == "Тестовая научная статья"
    assert article.metadata.authors[0].name == "Иванов И. И."
    assert article.metadata.abstracts[0].text == "Проверка преобразования DOCX."
    assert article.metadata.keywords == ["DOCX", "LaTeX", "статья"]
    assert any(isinstance(block, SectionBlock) for block in article.body)
    assert any(isinstance(block, ParagraphBlock) for block in article.body)
    assert any(isinstance(block, TableBlock) for block in article.body)
    assert any(isinstance(block, ListItemBlock) for block in article.body)


def test_docx_parser_preserves_inline_omml_inside_text_paragraph(
    tmp_path: Path,
) -> None:
    source = tmp_path / "inline-math.docx"
    document = Document()
    document.add_heading("Тестовая статья", level=0)
    document.add_heading("Методика", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("где ")
    math = OxmlElement("m:oMath")
    math_run = OxmlElement("m:r")
    math_text = OxmlElement("m:t")
    math_text.text = "m"
    math_run.append(math_text)
    math.append(math_run)
    paragraph._p.append(math)
    paragraph.add_run(" — поисковый метод.")
    document.save(source)

    article = DocxParser(source, tmp_path / "assets").parse()
    paragraphs = [
        block for block in article.body if isinstance(block, ParagraphBlock)
    ]

    assert len(paragraphs) == 1
    assert [run.text for run in paragraphs[0].runs] == [
        "где ",
        "",
        " — поисковый метод.",
    ]
    assert paragraphs[0].runs[1].math_latex == "m"
    assert not any(isinstance(block, EquationBlock) for block in article.body)
    assert ConversionValidator()._article_counts(article)["equations"] == 1


def test_docx_parser_recognizes_numbered_equation_table(
    tmp_path: Path,
) -> None:
    source = tmp_path / "equation-table.docx"
    document = Document()
    document.add_heading("Тестовая статья", level=0)
    table = document.add_table(rows=1, cols=2)
    paragraph = table.cell(0, 0).paragraphs[0]
    math_paragraph = OxmlElement("m:oMathPara")
    math = OxmlElement("m:oMath")
    math_run = OxmlElement("m:r")
    math_text = OxmlElement("m:t")
    math_text.text = "x"
    math_run.append(math_text)
    math.append(math_run)
    math_paragraph.append(math)
    paragraph._p.append(math_paragraph)
    table.cell(0, 1).text = "(7)"
    document.save(source)

    article = DocxParser(source, tmp_path / "assets").parse()
    equations = [
        block for block in article.body if isinstance(block, EquationBlock)
    ]

    assert len(equations) == 1
    assert equations[0].latex == "x"
    assert equations[0].number == "7"
    assert not any(isinstance(block, TableBlock) for block in article.body)
