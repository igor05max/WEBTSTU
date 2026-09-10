from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.template_workspace.v2.comparison.document_diff import DocumentDiffBuilder
from apps.template_workspace.v2.inspector.document import DocumentInspector


class Command(BaseCommand):
    help = "Inspect DOCX files with the isolated Word-first template V2 engine."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Source ARTICLE.docx")
        parser.add_argument("--template", help="Formatted/template DOCX to compare with source")
        parser.add_argument("--control", help="Optional additional formatted/control DOCX")
        parser.add_argument("--output", default="var/template_v2_analysis", help="Directory for JSON reports")

    def handle(self, *args, **options):
        output = Path(options["output"])
        output.mkdir(parents=True, exist_ok=True)
        source = DocumentInspector(options["source"]).inspect()
        self._write(output / "source.json", source.to_dict())
        self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'source.json'}"))

        if options.get("template"):
            template = DocumentInspector(options["template"]).inspect()
            diff = DocumentDiffBuilder().compare(source, template)
            self._write(output / "template.json", template.to_dict())
            self._write(output / "diff.json", diff.to_dict())
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'template.json'}"))
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'diff.json'}"))

        if options.get("control"):
            control = DocumentInspector(options["control"]).inspect()
            self._write(output / "control.json", control.to_dict())
            if options.get("template"):
                formatted_diff = DocumentDiffBuilder().compare(template, control)
                self._write(output / "formatted_control_diff.json", formatted_diff.to_dict())
            self.stdout.write(self.style.SUCCESS(f"Wrote {output / 'control.json'}"))

    @staticmethod
    def _write(path: Path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
