"""Framework-agnostic engine for scientific DOCX template processing."""

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
from .schema import BLOCK_CATALOG, normalize_template_rules

__all__ = [
    "BLOCK_CATALOG",
    "DocumentTemplateEngineError",
    "TEMPLATE_RULE_SCHEMA",
    "build_docx_from_template",
    "build_docx_plan",
    "build_interpretation_prompt",
    "check_docx_against_template",
    "interpret_template_text",
    "normalize_template_rules",
]
