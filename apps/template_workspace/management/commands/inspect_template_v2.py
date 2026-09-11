from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.submissions.document_conversion import convert_legacy_doc_to_docx
from apps.template_workspace.v2.ai import QwenProvider
from apps.template_workspace.v2.classification.roles import ROLE_NAMES, RoleClassifierV2
from apps.template_workspace.v2.comparison.document_diff import DocumentDiffBuilder
from apps.template_workspace.v2.editor.safe_word_editor import SafeWordEditor
from apps.template_workspace.v2.inspector.document import DocumentInspector
from apps.template_workspace.v2.mapping.preview import RoleMatcher
from apps.template_workspace.v2.profile.template import TemplateProfileBuilder


class Command(BaseCommand):
    help = "Inspect DOCX files with the isolated Word-first template V2 engine."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Source ARTICLE.docx")
        parser.add_argument("--template", help="Formatted/template DOCX to compare with source")
        parser.add_argument("--control", help="Optional additional formatted/control DOCX")
        parser.add_argument("--output", default="var/template_v2_analysis", help="Directory for JSON reports")
        parser.add_argument(
            "--reference-pair",
            action="store_true",
            help="Also write document_diff.json for source + formatted version of the same article.",
        )
        parser.add_argument(
            "--no-qwen",
            action="store_true",
            help="Disable V2 Qwen role refinement and use deterministic rules only.",
        )

    def handle(self, *args, **options):
        output = Path(options["output"])
        output.mkdir(parents=True, exist_ok=True)
        source_path = self._prepare_word_file(Path(options["source"]), output / "converted" / "article.docx")
        source = DocumentInspector(source_path).inspect()
        self._write(output / "article_report.json", source.to_dict())
        self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'article_report.json'}"))

        if options.get("template"):
            template_path = self._prepare_word_file(Path(options["template"]), output / "converted" / "template.docx")
            template = DocumentInspector(template_path).inspect()
            qwen_provider = None if options["no_qwen"] else QwenProvider(allowed_roles=ROLE_NAMES)
            classifier = RoleClassifierV2(use_ai=qwen_provider is not None, semantic_provider=qwen_provider)
            template_classifier = RoleClassifierV2(use_ai=False)
            article_structure = classifier.article_structure(source)
            template_profile = TemplateProfileBuilder(classifier=template_classifier).build(template)
            mapping_preview = RoleMatcher().build_preview(article_structure, template_profile)
            editor_result = SafeWordEditor(classifier=classifier).render(
                article_path=source_path,
                template_path=template_path,
                output_path=output / "result.docx",
                article_report=source,
                template_report=template,
                article_structure=article_structure,
                template_profile=template_profile,
                mapping_preview=mapping_preview,
                copy_template_headers=True,
            )
            self._write(output / "template_report.json", template.to_dict())
            self._write(output / "article_structure.json", article_structure.to_dict())
            self._write(output / "template_profile.json", template_profile.to_dict())
            self._write(output / "mapping_preview.json", mapping_preview.to_dict())
            self._write(output / "editor_report.json", editor_result.to_dict())
            self._write(output / "qwen_report.json", article_structure.diagnostics)
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'template_report.json'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'article_structure.json'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'template_profile.json'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'mapping_preview.json'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'result.docx'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'editor_report.json'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'qwen_report.json'}"))
            if options["reference_pair"]:
                diff = DocumentDiffBuilder().compare(source, template)
                self._write(output / "document_diff.json", diff.to_dict())
                self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'document_diff.json'}"))

        if options.get("control"):
            control = DocumentInspector(options["control"]).inspect()
            self._write(output / "control.json", control.to_dict())
            if options.get("template") and options["reference_pair"]:
                formatted_diff = DocumentDiffBuilder().compare(template, control)
                self._write(output / "formatted_control_diff.json", formatted_diff.to_dict())
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'control.json'}"))

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
