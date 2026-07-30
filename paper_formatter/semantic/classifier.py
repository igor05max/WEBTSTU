from __future__ import annotations

from pathlib import Path

from paper_formatter.config import SemanticSettings
from paper_formatter.semantic.models import SemanticAnalysis, SemanticBlock
from paper_formatter.semantic.rules import RuleSemanticClassifier


class HybridSemanticClassifier:
    """Compatibility wrapper around the deterministic semantic classifier."""

    def __init__(self, settings: SemanticSettings, cache_dir: Path | None = None) -> None:
        self.settings = settings
        self.rules = RuleSemanticClassifier()
        self.cache_dir = cache_dir

    def analyze_document(
        self,
        blocks: list[SemanticBlock],
        *,
        document_name: str,
    ) -> SemanticAnalysis:
        rule_analysis = self.rules.analyze_document(blocks, document_name=document_name)
        return SemanticAnalysis(
            provider="rules-only",
            decisions=rule_analysis.decisions,
            warnings=rule_analysis.warnings,
        )
