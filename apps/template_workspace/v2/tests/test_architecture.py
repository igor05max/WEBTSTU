from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import TestCase
from docx import Document

from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.inspector.document import DocumentInspector
from apps.template_workspace.v2.mapping.preview import RoleMatcher
from apps.template_workspace.v2.profile.template import TemplateProfileBuilder


class TemplateV2ArchitectureTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _docx(self, name: str, paragraphs: list[str]) -> Path:
        path = Path(self.tmp.name) / name
        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        document.save(path)
        return path

    def test_raw_inspector_is_offline_and_has_no_semantic_roles(self):
        path = self._docx("article.docx", ["Title", "Fig. 4 presents ordinary text."])
        report = DocumentInspector(path).inspect()
        self.assertIsNone(report.semantic_roles)
        self.assertEqual(report.fingerprint["paragraph_count"], 2)

    def test_real_template_application_does_not_create_content_diff(self):
        article = DocumentInspector(self._docx("article.docx", ["Article title", "1. Introduction", "Body text."])).inspect()
        template = DocumentInspector(self._docx("template.docx", ["Template title", "Author Name", "Abstract. Text."])).inspect()
        classifier = RoleClassifierV2(use_ai=False)
        structure = classifier.article_structure(article)
        profile = TemplateProfileBuilder(classifier=classifier).build(template)
        mapping = RoleMatcher().build_preview(structure, profile)
        self.assertGreater(mapping.summary["total_mappings"], 0)
        self.assertIn("Mapping preview does not compare ARTICLE and TEMPLATE", mapping.warnings[0])

