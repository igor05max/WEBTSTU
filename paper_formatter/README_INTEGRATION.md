# Paper formatter integration

This package is the deterministic document conversion engine ported from
`System_doc_latex/paper_formatter_cli_v3`.

The web workflow calls `ConversionPipeline` for DOCX submissions whenever the
original formatting-template file is available. It:

- parses the source into `ArticleIR`;
- derives a measurable profile from DOCX, TEX, PDF, ZIP or text requirements;
- rebuilds an editable DOCX and a portable LaTeX project;
- validates preservation of text, formulas, tables, figures and references.

Document conversion is local and rule-based. It never sends article blocks to a
remote model. Application-level semantic reviews use the configured local Qwen
OpenAI-compatible endpoint through `apps.checks.ai_client`.

If a historical template has only normalized rules and no original supported
file, the existing `document_template_engine` remains the compatibility
fallback.
