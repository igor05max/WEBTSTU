from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.submissions.document_conversion import convert_legacy_doc_to_docx
from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.editor.safe_word_editor import SafeWordEditor
from apps.template_workspace.v2.inspector.document import DocumentInspector
from apps.template_workspace.v2.mapping.preview import RoleMatcher
from apps.template_workspace.v2.profile.template import TemplateProfileBuilder


class Command(BaseCommand):
    help = "Run isolated Word-first Template V2 for ARTICLE + TEMPLATE without the web UI."

    def add_arguments(self, parser):
        parser.add_argument("article", help="ARTICLE.docx or ARTICLE.doc")
        parser.add_argument("template", help="TEMPLATE.docx or TEMPLATE.doc")
        parser.add_argument("--output", default="var/template_v2_debug", help="Output directory, or an explicit RESULT.docx path")

    def handle(self, *args, **options):
        output_option = Path(options["output"])
        if output_option.suffix.casefold() == ".docx":
            result_path = output_option
            output = output_option.parent
        else:
            output = output_option
            result_path = output / "result.docx"
        output.mkdir(parents=True, exist_ok=True)

        article_path = self._prepare_word_file(Path(options["article"]), output / "converted" / "article.docx")
        template_path = self._prepare_word_file(Path(options["template"]), output / "converted" / "template.docx")

        article_report = DocumentInspector(article_path).inspect()
        template_report = DocumentInspector(template_path).inspect()
        classifier = RoleClassifierV2(use_ai=False)
        article_structure = classifier.article_structure(article_report)
        template_profile = TemplateProfileBuilder(classifier=classifier).build(template_report)
        mapping_preview = RoleMatcher().build_preview(article_structure, template_profile)
        editor_result = SafeWordEditor(classifier=classifier).render(
            article_path=article_path,
            template_path=template_path,
            output_path=result_path,
            article_report=article_report,
            template_report=template_report,
            article_structure=article_structure,
            template_profile=template_profile,
            mapping_preview=mapping_preview,
        )

        self._write(output / "article_report.json", article_report.to_dict())
        self._write(output / "template_report.json", template_report.to_dict())
        self._write(output / "article_structure.json", article_structure.to_dict())
        self._write(output / "template_profile.json", template_profile.to_dict())
        self._write(output / "mapping_preview.json", mapping_preview.to_dict())
        self._write(output / "editor_report.json", editor_result.to_dict())

        self.stdout.write(self.style.SUCCESS(f"Wrote {result_path}"))
        self.stdout.write(self.style.SUCCESS(f"Wrote reports to {output}"))

    @staticmethod
    def _write(path: Path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _prepare_word_file(path: Path, output: Path) -> Path:
        if path.suffix.casefold() == ".docx":
            return path
        if path.suffix.casefold() != ".doc":
            raise ValueError("V2 accepts only DOCX or DOC files.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(convert_legacy_doc_to_docx(path.read_bytes()))
        return output
