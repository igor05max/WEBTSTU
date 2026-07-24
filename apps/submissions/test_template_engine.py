from io import BytesIO

from django.test import SimpleTestCase

from document_template_engine import (
    build_docx_from_template,
    build_docx_plan,
    build_latex_template,
    check_latex_against_template,
    extract_latex_template_rules,
    interpret_template_text,
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
    def test_latex_rules_are_extracted_without_executing_source(self):
        source = r"""
            \documentclass[14pt,a4paper]{article}
            \usepackage[top=2cm,right=1.5cm,bottom=2cm,left=2.5cm]{geometry}
            \usepackage{fontspec}
            \setmainfont{Times New Roman}
            \usepackage{setspace}
            \setstretch{1.5}
            \setlength{\parindent}{1cm}
            \title{НАЗВАНИЕ}
            \author{И.О. Фамилия}
            \begin{document}
            \section{Введение}
            Научный текст.
            \end{document}
        """

        rules = extract_latex_template_rules(source)

        self.assertEqual(rules["page"]["size"], "A4")
        self.assertEqual(rules["page"]["margins_cm"]["right"], 1.5)
        self.assertEqual(rules["body"]["font_family"], "Times New Roman")
        self.assertEqual(rules["body"]["font_size_pt"], 14)
        self.assertEqual(rules["body"]["line_spacing"], 1.5)
        self.assertEqual(rules["body"]["first_line_indent_cm"], 1)
        self.assertEqual(rules["structure"]["required_sections"], ["Введение"])

    def test_generated_latex_uses_rules_and_passes_layout_check(self):
        rules = {
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
            "document": {
                "blocks": [
                    {"role": "title", "required": True},
                    {"role": "authors", "required": True},
                    {"role": "abstract", "required": True},
                    {"role": "keywords", "required": True},
                    {"role": "body", "required": True},
                ]
            },
        }

        source = build_latex_template(
            rules,
            metadata={
                "title": "НАЗВАНИЕ РАБОТЫ",
                "authors": "И.О. Фамилия",
                "abstract": "Краткое описание работы.",
                "keywords": "анализ; система",
            },
        )
        report = check_latex_against_template(source, rules)

        decoded = source.decode("utf-8")
        self.assertIn(r"\setmainfont{Times New Roman}", decoded)
        self.assertIn(r"\usepackage[a4paper,top=2cm,right=2cm,bottom=2cm,left=2cm]{geometry}", decoded)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["metrics"]["source_format"], "latex")

    def test_title_centering_does_not_change_detected_body_alignment(self):
        source = r"""
            \documentclass[12pt]{article}
            \begin{document}
            {\centering НАЗВАНИЕ\par}
            Основной текст выровнен по ширине по умолчанию.
            \end{document}
        """

        rules = extract_latex_template_rules(source)

        self.assertEqual(rules["body"]["alignment"], "justify")
        self.assertEqual(rules["body"]["font_size_pt"], 12)

    def test_ai_unspecified_false_values_do_not_erase_author_formatting(self):
        rules = interpret_template_text(
            document_type="Тезисы",
            target_name="Конференция",
            text="Список литературы при необходимости оформляется по СТБ.",
            complete_json=lambda _prompt: """
                {
                  "document": {
                    "blocks": [
                      {
                        "role": "title",
                        "required": true,
                        "style": {
                          "first_line_indent_cm": null,
                          "bold": false,
                          "italic": false
                        }
                      },
                      {
                        "role": "authors",
                        "required": true,
                        "style": {"line_spacing": 2}
                      },
                      {
                        "role": "references",
                        "required": false,
                        "style": {
                          "alignment": "justify",
                          "first_line_indent_cm": 1,
                          "bold": false
                        }
                      }
                    ]
                  },
                  "body": {"line_spacing": 1}
                }
            """,
        )

        blocks = {
            block["role"]: block
            for block in rules["document"]["blocks"]
        }
        self.assertNotIn("bold", blocks["title"]["style"])
        self.assertNotIn("italic", blocks["title"]["style"])
        self.assertEqual(blocks["title"]["style"]["first_line_indent_cm"], 0)
        self.assertEqual(blocks["authors"]["style"]["line_spacing"], 1)
        self.assertNotIn("alignment", blocks["references"]["style"])
        self.assertNotIn("first_line_indent_cm", blocks["references"]["style"])

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
