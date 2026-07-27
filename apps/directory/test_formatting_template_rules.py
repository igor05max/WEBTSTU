import io

from django.test import SimpleTestCase

from apps.directory.formatting_templates import _extract_docx_rules


class DocxFormattingRuleExtractionTests(SimpleTestCase):
    def test_uses_inherited_body_style_instead_of_reference_overrides(self):
        from docx import Document
        from docx.enum.style import WD_STYLE_TYPE
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Cm, Pt

        document = Document()
        normal = document.styles["Normal"]
        normal.font.name = "Palatino Linotype"
        normal.font.size = Pt(10)

        body_style = document.styles.add_style("MDPI_3.1_text", WD_STYLE_TYPE.PARAGRAPH)
        body_style.base_style = normal
        body_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        body_style.paragraph_format.first_line_indent = Cm(0.75)
        body_style.paragraph_format.line_spacing = Pt(14)

        heading_style = document.styles.add_style(
            "MDPI_2.1_heading1",
            WD_STYLE_TYPE.PARAGRAPH,
        )
        heading_style.base_style = normal
        heading_style.font.size = Pt(12)

        title_style = document.styles.add_style(
            "MDPI_1.2_title",
            WD_STYLE_TYPE.PARAGRAPH,
        )
        title_style.base_style = normal
        title_style.font.size = Pt(24)

        reference_style = document.styles.add_style(
            "MDPI_8.1_references",
            WD_STYLE_TYPE.PARAGRAPH,
        )
        reference_style.base_style = normal
        reference_style.font.size = Pt(9)
        reference_style.paragraph_format.first_line_indent = Cm(-0.75)
        reference_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

        document.add_paragraph("Title", style=title_style)
        document.add_paragraph("1. Introduction", style=heading_style)
        for index in range(5):
            document.add_paragraph(f"Main text paragraph {index}.", style=body_style)
        for index in range(8):
            document.add_paragraph(f"Reference {index}.", style=reference_style)

        output = io.BytesIO()
        document.save(output)
        rules = _extract_docx_rules(output.getvalue())

        self.assertEqual(rules["body"]["font_family"], "Palatino Linotype")
        self.assertEqual(rules["body"]["font_size_pt"], 10.0)
        self.assertEqual(rules["body"]["line_spacing"], 1.4)
        self.assertEqual(rules["body"]["first_line_indent_cm"], 0.75)
        self.assertEqual(rules["body"]["alignment"], "justify")
        self.assertEqual(rules["headings"]["font_size_pt"], 12.0)
        self.assertEqual(rules["headings"]["title_font_size_pt"], 24.0)

    def test_reads_complex_script_size_used_by_mdpi_template(self):
        from docx import Document
        from docx.enum.style import WD_STYLE_TYPE
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Cm, Pt

        document = Document()
        normal = document.styles["Normal"]
        normal.font.name = None
        normal.font.size = None

        body_style = document.styles.add_style(
            "MDPI_3.1_text",
            WD_STYLE_TYPE.PARAGRAPH,
        )
        body_style.font.name = "Palatino Linotype"
        run_properties = body_style.element.get_or_add_rPr()
        complex_script_size = OxmlElement("w:szCs")
        complex_script_size.set(qn("w:val"), "22")
        run_properties.append(complex_script_size)
        body_style.paragraph_format.line_spacing = Pt(14)
        body_style.paragraph_format.first_line_indent = Cm(0.75)
        body_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        paragraph = document.add_paragraph(style=body_style)
        paragraph.add_run("A representative MDPI body paragraph.")

        output = io.BytesIO()
        document.save(output)
        rules = _extract_docx_rules(output.getvalue())

        self.assertEqual(rules["body"]["font_size_pt"], 11.0)
        self.assertEqual(rules["body"]["line_spacing"], 1.27)
