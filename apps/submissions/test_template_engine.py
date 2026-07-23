from io import BytesIO

from django.test import SimpleTestCase

from document_template_engine import (
    build_docx_from_template,
    build_docx_plan,
    normalize_template_rules,
)


LEGACY_RULES = {
    "page": {
        "size": "A4",
        "orientation": "portrait",
        "margins_cm": {"top": 2, "right": 2, "bottom": 2, "left": 2},
    },
    "body": {
        "font_family": "Times New Roman",
        "font_size_pt": 14,
        "line_spacing": 1,
        "first_line_indent_cm": 1,
        "alignment": "justify",
    },
    "structure": {
        "required_sections": [
            "UDC index",
            "Title",
            "Author initials and surname",
            "Scientific supervisor (optional)",
            "Institution name",
            "City, Country",
            "Abstract text",
            "References (optional)",
        ],
    },
    "metadata": {
        "required_fields": [
            "UDC index",
            "Title",
            "Author initials and surname",
            "Institution name",
            "City",
            "Country",
        ]
    },
}


def _sample_docx():
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    document = Document()
    for section in document.sections:
        section.top_margin = Cm(2)
        section.right_margin = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(2)
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(14)
    normal.paragraph_format.first_line_indent = Cm(1)
    normal.paragraph_format.line_spacing = 1

    values = [
        ("УДК 004.8", WD_ALIGN_PARAGRAPH.LEFT, 0),
        ("ИНТЕЛЛЕКТУАЛЬНАЯ СИСТЕМА АНАЛИЗА ВИДЕО", WD_ALIGN_PARAGRAPH.CENTER, 0),
        ("А.А. АРХИПОВ, И.И. МАКСИМОВ", WD_ALIGN_PARAGRAPH.CENTER, 0),
        ("Научный руководитель А. Д. ОБУХОВ", WD_ALIGN_PARAGRAPH.CENTER, 0),
        ("Тамбовский государственный технический университет", WD_ALIGN_PARAGRAPH.CENTER, 0),
        ("Тамбов, Россия", WD_ALIGN_PARAGRAPH.CENTER, 0),
        (
            "Основной научный текст сохраняется без изменения содержания и фактов.",
            WD_ALIGN_PARAGRAPH.JUSTIFY,
            1,
        ),
        ("СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ", WD_ALIGN_PARAGRAPH.CENTER, 0),
        ("1. Иванов И. И. Анализ документов. 2026.", WD_ALIGN_PARAGRAPH.LEFT, 0),
    ]
    for text, alignment, indent in values:
        paragraph = document.add_paragraph(text)
        paragraph.alignment = alignment
        paragraph.paragraph_format.first_line_indent = Cm(indent)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class ReusableTemplateEngineTests(SimpleTestCase):
    def test_legacy_placeholders_become_blocks_not_required_sections(self):
        normalized = normalize_template_rules(LEGACY_RULES)

        self.assertEqual(normalized["schema_version"], "2.0")
        self.assertEqual(normalized["structure"]["required_sections"], [])
        blocks = {
            block["role"]: block
            for block in normalized["document"]["blocks"]
        }
        self.assertTrue(blocks["udc"]["required"])
        self.assertTrue(blocks["title"]["required"])
        self.assertTrue(blocks["body"]["required"])
        self.assertFalse(blocks["supervisor"]["required"])
        self.assertFalse(blocks["references"]["required"])

    def test_plan_recognizes_title_block_without_false_missing_sections(self):
        plan = build_docx_plan(_sample_docx(), LEGACY_RULES)

        self.assertEqual(plan["issues"], [])
        found = {
            block["role"]
            for block in plan["blocks"]
            if block["found"]
        }
        self.assertTrue(
            {"udc", "title", "authors", "supervisor", "institution", "city_country", "body", "references"}
            <= found
        )

    def test_builder_formats_roles_separately_and_preserves_text(self):
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        source = _sample_docx()
        built, changes, plan = build_docx_from_template(source, LEGACY_RULES)
        original = Document(BytesIO(source))
        result = Document(BytesIO(built))

        self.assertEqual(
            [paragraph.text for paragraph in result.paragraphs],
            [paragraph.text for paragraph in original.paragraphs],
        )
        self.assertEqual(result.paragraphs[0].alignment, WD_ALIGN_PARAGRAPH.LEFT)
        self.assertAlmostEqual(
            result.paragraphs[0].paragraph_format.first_line_indent.cm,
            0,
            places=1,
        )
        self.assertEqual(result.paragraphs[1].alignment, WD_ALIGN_PARAGRAPH.CENTER)
        self.assertAlmostEqual(
            result.paragraphs[1].paragraph_format.first_line_indent.cm,
            0,
            places=1,
        )
        self.assertEqual(result.paragraphs[6].alignment, WD_ALIGN_PARAGRAPH.JUSTIFY)
        self.assertAlmostEqual(
            result.paragraphs[6].paragraph_format.first_line_indent.cm,
            1,
            places=1,
        )
        self.assertEqual(result.paragraphs[7].alignment, WD_ALIGN_PARAGRAPH.CENTER)
        self.assertEqual(plan["issues"], [])
        self.assertIn("титульный блок оформлен отдельно от основного текста", changes)
