"""Framework-agnostic engine for scientific DOCX and LaTeX processing."""

from .engine import (
    DocumentTemplateEngineError,
    build_docx_from_template,
    build_docx_plan,
    check_docx_against_template,
)
from .interpretation import (
    TEMPLATE_RULE_SCHEMA,
    build_interpretation_prompt,
    interpret_template_text,
)
from .latex import (
    build_latex_template,
    check_latex_against_template,
    extract_latex_template_rules,
    latex_to_plain_text,
)
from .schema import BLOCK_CATALOG, normalize_template_rules

__all__ = [
    "BLOCK_CATALOG",
    "DocumentTemplateEngineError",
    "TEMPLATE_RULE_SCHEMA",
    "build_docx_from_template",
    "build_docx_plan",
    "build_interpretation_prompt",
    "build_latex_template",
    "check_latex_against_template",
    "check_docx_against_template",
    "extract_latex_template_rules",
    "interpret_template_text",
    "latex_to_plain_text",
    "normalize_template_rules",
]
